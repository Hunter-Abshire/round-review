"""Review one window in two passes: read the situation, then coach against the checklist."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from round_review.coaching.context import PlayerContext, merge_context
from round_review.coaching.frames import select_situation_frames
from round_review.coaching.knowledge import CoachingKnowledge, relevant_categories
from round_review.coaching.parse import Finding, parse_findings
from round_review.coaching.prompt import (
    FINDING_SCHEMA,
    RETRY_NUDGE,
    SITUATION_SCHEMA,
    SITUATION_SYSTEM_PROMPT,
    build_coach_prompt,
    build_situation_prompt,
    build_system_prompt,
)
from round_review.coaching.situation import Situation, parse_situation
from round_review.errors import ParseError
from round_review.llm.client import build_chat_request, send_review
from round_review.llm.transport import Transport
from round_review.video.frames import FrameSample, encode_frame_b64
from round_review.video.windows import Window
from round_review.vision.hud import HudRead, constrain_phase

log = logging.getLogger(__name__)


def _relevant_findings(
    findings: list[Finding], situation: Situation | None
) -> tuple[list[Finding], list[str]]:
    allowed = relevant_categories(situation.phase if situation else None)
    kept: list[Finding] = []
    warnings: list[str] = []
    for finding in findings:
        # Check ids carry the authoritative category; model labels can disagree.
        category = finding.check_id.partition(".")[0]
        reason = None
        if allowed is not None and category != "other" and category not in allowed:
            reason = "check does not apply to the detected round phase"
        elif category == "crosshair" or finding.category == "crosshair":
            weapon = (situation.weapon or "").lower() if situation else ""
            if not weapon or any(w in weapon for w in ("knife", "melee", "spike", "ability")):
                reason = "crosshair criticism requires a visible firearm"
        if reason:
            warnings.append(f"dropped finding at t={finding.timestamp_s:.1f}s: {reason}")
        else:
            kept.append(finding)
    return kept, warnings


@dataclass(frozen=True, slots=True)
class WindowResult:
    window: Window
    samples: tuple[FrameSample, ...]
    findings: tuple[Finding, ...]
    model_calls: int
    warnings: tuple[str, ...]
    situation: Situation | None = None
    context: PlayerContext = field(default_factory=PlayerContext)
    # True only when the coach reply could not be parsed. An abstained window (buy phase,
    # spectating) has no findings and a warning, but is a successful review of that window.
    parse_failed: bool = False
    # Why coaching was skipped for this window, if it was. Kept separate from the warning
    # text so the report can count reasons and diagnose a model that misreads the screen.
    abstained_reason: str | None = None
    # True when the deterministic HUD read corrected the model's round phase.
    hud_override: bool = False

    @property
    def abstained(self) -> bool:
        return self.abstained_reason is not None


def review_window(
    window: Window,
    samples: Sequence[FrameSample],
    transport: Transport,
    model: str,
    calls_today: int,
    cap: int,
    timeout_s: float,
    context: PlayerContext | None,
    knowledge: CoachingKnowledge,
    situation_pass: bool = True,
    hud: HudRead | None = None,
    buy_phase_max_s: float = 45.0,
    hud_min_confidence: float = 0.8,
    situation_frames: int = 3,
    coach_frames: int = 0,
) -> WindowResult:
    """Pass 1 (optional) asks the model to describe what is on screen; a failed pass 1 is a
    warning. Pass 2 coaches against the checklist and retries once with a JSON-only nudge;
    a second failure raises ParseError carrying the number of calls spent. Every call counts
    against the daily cap."""
    context = context or PlayerContext()
    coach_samples = select_situation_frames(samples, coach_frames)
    images = [encode_frame_b64(s.path) for s in coach_samples]
    warnings: list[str] = []
    calls = 0
    situation: Situation | None = None
    hud_override = False

    if situation_pass:
        scene_samples = select_situation_frames(samples, situation_frames)
        request = build_chat_request(
            model,
            SITUATION_SYSTEM_PROMPT,
            build_situation_prompt(window, scene_samples, context),
            [encode_frame_b64(s.path) for s in scene_samples],
            SITUATION_SCHEMA,
            timeout_s,
        )
        response = send_review(request, transport, calls_today + calls, cap)
        calls += 1
        try:
            situation = parse_situation(response.content)
        except ParseError as exc:
            log.warning("window %d situation pass unparseable: %s", window.index, exc)
            warnings.append(f"situation pass unparseable, coaching without it: {exc}")
        else:
            context = merge_context(context, situation.to_context())

        if situation is not None:
            # The clock is ground truth the model cannot argue with: a round timer above the
            # buy phase maximum proves the round is live, whatever the model called it.
            verdict = constrain_phase(situation.phase, hud, buy_phase_max_s, hud_min_confidence)
            if verdict.overridden:
                situation = replace(situation, phase=verdict.phase)
                hud_override = True
                warnings.append(f"HUD override: {verdict.reason}")

        if situation is not None and situation.phase in (None, "pre_round", "spectating"):
            reason = {"pre_round": "buy phase", "spectating": "spectating another player"}.get(
                situation.phase or "", "round phase unreadable"
            )
            warnings.append(f"coaching skipped: {reason}; no supported combat decision to judge")
            return WindowResult(
                window,
                tuple(samples),
                (),
                calls,
                tuple(warnings),
                situation,
                context,
                abstained_reason=reason,
                hud_override=hud_override,
            )

    system = build_system_prompt(knowledge, situation.phase if situation else None)
    prompt = build_coach_prompt(window, coach_samples, context, situation, knowledge)
    check_ids = knowledge.checklist.check_ids()
    last_error: ParseError | None = None
    for attempt in range(2):
        text = prompt if attempt == 0 else prompt + RETRY_NUDGE
        request = build_chat_request(model, system, text, images, FINDING_SCHEMA, timeout_s)
        response = send_review(request, transport, calls_today + calls, cap)
        calls += 1
        try:
            findings, parse_warnings = parse_findings(response.content, window, samples, check_ids)
        except ParseError as exc:
            last_error = exc
            log.warning(
                "window %d coach attempt %d unparseable: %s", window.index, attempt + 1, exc
            )
            warnings.append(f"coach attempt {attempt + 1} unparseable, retry issued: {exc}")
            continue
        if situation_pass:
            findings, relevance_warnings = _relevant_findings(findings, situation)
            parse_warnings.extend(relevance_warnings)
        return WindowResult(
            window,
            tuple(samples),
            tuple(findings),
            calls,
            tuple(warnings + parse_warnings),
            situation,
            context,
            hud_override=hud_override,
        )

    assert last_error is not None
    raise ParseError(
        f"window {window.index}: model output unparseable after retry: {last_error}",
        model_calls=calls,
    )
