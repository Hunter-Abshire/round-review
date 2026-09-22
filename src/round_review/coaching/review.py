"""Review one window in two passes: read the situation, then coach against the checklist."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from round_review.coaching.abilities import foreign_abilities
from round_review.coaching.context import PlayerContext, merge_context
from round_review.coaching.frames import select_situation_frames
from round_review.coaching.knowledge import (
    AgentBrief,
    CoachingKnowledge,
    find_agent,
    relevant_categories,
)
from round_review.coaching.parse import Finding, Strength, parse_findings, parse_strengths
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
from round_review.errors import OllamaError, ParseError
from round_review.llm.client import build_chat_request, send_review
from round_review.llm.transport import Transport
from round_review.video.frames import FrameSample, encode_frame_b64
from round_review.video.windows import Window
from round_review.vision.hud import HudRead, constrain_phase
from round_review.vision.state import HudState

log = logging.getLogger(__name__)


# Trade distance is a claim about a fight. With nobody on screen and nothing in the timeline,
# it is a guess about where people might be, which is exactly what the player cannot act on.
CONTACT_ONLY_CATEGORIES: frozenset[str] = frozenset({"trading"})

# Categories that tell the player to act: move, hold elsewhere, spend utility, trade. With
# the round already decided on numbers there is nothing to act on, so the advice is noise.
ACTION_CATEGORIES: frozenset[str] = frozenset(
    {"positioning", "timing", "trading", "peeking", "utility", "info"}
)
# Two more bodies than the enemy with nobody on screen. Deliberately blunt: a 4v3 is still
# a round worth coaching, and the counts come from a model reading a scoreboard.
DECISIVE_ADVANTAGE = 2


def _needs_contact(finding: Finding) -> bool:
    category = finding.check_id.partition(".")[0]
    return category in CONTACT_ONLY_CATEGORIES or finding.category in CONTACT_ONLY_CATEGORIES


def _decided_round(situation: Situation | None) -> bool:
    """True when the player's side is far enough ahead that waiting wins the round.

    Never in the opening of a round: seven seconds after the barrier drops nobody has a
    decisive advantage, and trusting the model's scoreboard read there cost two real
    findings in a measured review.
    """
    if situation is None or situation.enemies_visible:
        return False
    if situation.phase == "early":
        return False
    enemies = situation.enemies_alive
    mates = situation.teammates_alive
    if enemies is None or mates is None:
        return False
    # teammates_alive may or may not count the player, so compare on the pessimistic reading.
    return mates - enemies >= DECISIVE_ADVANTAGE


# Ollama reports this as a 400 with the token counts in the body. Matching on the text is
# unpleasant but the alternative is losing a whole review to one oversized window.
CONTEXT_ERROR_MARKERS: tuple[str, ...] = ("exceeds the available context", "exceed_context_size")


def _too_big_for_context(error: OllamaError) -> bool:
    text = str(error).lower()
    return any(marker.lower() in text for marker in CONTEXT_ERROR_MARKERS)


def _halve(images: list[str]) -> list[str]:
    """Keep every other picture, so the ones kept still span the window."""
    return images[::2] if len(images) > 2 else images[:1]


def _relevant_findings(
    findings: list[Finding],
    situation: Situation | None,
    agent: str | None = None,
    agents: Mapping[str, AgentBrief] | None = None,
    state: HudState | None = None,
) -> tuple[list[Finding], list[str]]:
    allowed = relevant_categories(situation.phase if situation else None)
    decided = _decided_round(situation)
    # An icon that is dark is an ability that is gone. Telling the player to throw it is
    # the most confident kind of wrong a coach can be.
    nothing_up = state is not None and bool(state.abilities_lit) and not any(state.abilities_lit)
    kept: list[Finding] = []
    warnings: list[str] = []
    for finding in findings:
        # Check ids carry the authoritative category; model labels can disagree.
        category = finding.check_id.partition(".")[0]
        reason = None
        foreign = (
            foreign_abilities(
                f"{finding.observation}\n{finding.suggested_alternative}", agent, agents
            )
            if agents is not None
            else ()
        )
        if foreign:
            reason = (
                f"names {', '.join(foreign)}, which {agent} does not have; "
                "the agent was probably misread"
            )
        elif allowed is not None and category != "other" and category not in allowed:
            reason = "check does not apply to the detected round phase"
        elif nothing_up and (category == "utility" or finding.category == "utility"):
            reason = "the HUD shows no ability was available at this moment"
        elif decided and (category in ACTION_CATEGORIES or finding.category in ACTION_CATEGORIES):
            reason = (
                "the player's side held a decisive numbers advantage with no enemy on "
                "screen, so waiting was the correct play"
            )
        elif _needs_contact(finding) and situation is not None and not situation.enemies_visible:
            reason = "no enemy was on screen, so trade distance is a guess about this moment"
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
    strengths: tuple[Strength, ...]
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
    state: HudState | None = None,
    live_round: bool = False,
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
    clock_correction: str | None = None

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
            # The player knows which agent they picked; the model is reading a portrait at
            # 1280px. Correct the read so the brief and the situation tell one story.
            told = find_agent(knowledge.agents, context.agent)
            read = find_agent(knowledge.agents, situation.agent)
            if context.agent and situation.agent and told is not read:
                warnings.append(
                    f"situation pass read the agent as {situation.agent}; using "
                    f"{context.agent} as given"
                )
                situation = replace(situation, agent=context.agent)
            context = merge_context(context, situation.to_context())

        if situation is not None:
            # The clock is ground truth the model cannot argue with: a round timer above the
            # buy phase maximum proves the round is live, whatever the model called it.
            verdict = constrain_phase(
                situation.phase, hud, buy_phase_max_s, hud_min_confidence, live_round
            )
            if verdict.overridden:
                claimed = situation.phase or "unreadable"
                situation = replace(situation, phase=verdict.phase)
                hud_override = True
                warnings.append(f"HUD override: {verdict.reason}")
                clock = hud.clock_text if hud else None
                proof = (
                    f"the round clock says {clock}"
                    if clock
                    else "this moment sits inside a live round, between the barrier dropping "
                    "and the next buy phase"
                )
                clock_correction = (
                    f"Correction, measured rather than inferred: {proof}, so this is live "
                    f"play, not the buy phase. The scene read below called it {claimed} and "
                    f"its summary may repeat that. It is wrong. Judge this as a "
                    f"{verdict.phase} moment in a live round and ignore any claim that the "
                    "barrier is up or that the round has not started."
                )

        # A buy window is planned on the buy phase deliberately: it is the only moment the
        # purchase, the credits and the team's loadout are on screen, so it is coached on
        # economy rather than skipped like every other pre-round window.
        buying = (
            window.source == "buy" and situation is not None and situation.phase != "spectating"
        )
        if (
            not buying
            and situation is not None
            and situation.phase in (None, "pre_round", "spectating")
        ):
            reason = {"pre_round": "buy phase", "spectating": "spectating another player"}.get(
                situation.phase or "", "round phase unreadable"
            )
            warnings.append(f"coaching skipped: {reason}; no supported combat decision to judge")
            return WindowResult(
                window,
                tuple(samples),
                (),
                (),
                calls,
                tuple(warnings),
                situation,
                context,
                abstained_reason=reason,
                hud_override=hud_override,
            )

    buy_phase = window.source == "buy"
    system = build_system_prompt(
        knowledge,
        "buy" if buy_phase else (situation.phase if situation else None),
    )
    prompt = build_coach_prompt(
        window, coach_samples, context, situation, knowledge, state, clock_correction
    )
    check_ids = knowledge.checklist.check_ids()
    last_error: ParseError | None = None
    for attempt in range(2):
        text = prompt if attempt == 0 else prompt + RETRY_NUDGE
        request = build_chat_request(model, system, text, images, FINDING_SCHEMA, timeout_s)
        try:
            response = send_review(request, transport, calls_today + calls, cap)
        except OllamaError as exc:
            calls += 1
            if not _too_big_for_context(exc) or len(images) <= 1:
                raise
            # Half the pictures is a worse review of this window. No review of it is worse
            # still, and the whole file fails on the first window that does not fit.
            images = _halve(images)
            warnings.append(
                f"window did not fit the model's context, retried on {len(images)} frame(s); "
                "lower coach_frames or raise num_ctx to stop losing detail here"
            )
            log.warning("window %d exceeded num_ctx, retrying with fewer frames", window.index)
            request = build_chat_request(model, system, text, images, FINDING_SCHEMA, timeout_s)
            response = send_review(request, transport, calls_today + calls, cap)
        calls += 1
        try:
            findings, parse_warnings = parse_findings(response.content, window, samples, check_ids)
            strengths, strength_warnings = parse_strengths(
                response.content, window, samples, check_ids
            )
            parse_warnings.extend(strength_warnings)
        except ParseError as exc:
            last_error = exc
            log.warning(
                "window %d coach attempt %d unparseable: %s", window.index, attempt + 1, exc
            )
            warnings.append(f"coach attempt {attempt + 1} unparseable, retry issued: {exc}")
            continue
        if situation_pass:
            findings, relevance_warnings = _relevant_findings(
                findings, situation, context.agent, knowledge.agents, state
            )
            parse_warnings.extend(relevance_warnings)
        return WindowResult(
            window,
            tuple(samples),
            tuple(findings),
            tuple(strengths),
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
