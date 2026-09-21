"""Review one window in two passes: read the situation, then coach against the checklist."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from round_review.coaching.context import PlayerContext, merge_context
from round_review.coaching.knowledge import CoachingKnowledge
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

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WindowResult:
    window: Window
    samples: tuple[FrameSample, ...]
    findings: tuple[Finding, ...]
    model_calls: int
    warnings: tuple[str, ...]
    situation: Situation | None = None
    context: PlayerContext = field(default_factory=PlayerContext)


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
) -> WindowResult:
    """Pass 1 (optional) asks the model to describe what is on screen; a failed pass 1 is a
    warning. Pass 2 coaches against the checklist and retries once with a JSON-only nudge;
    a second failure raises ParseError carrying the number of calls spent. Every call counts
    against the daily cap."""
    context = context or PlayerContext()
    images = [encode_frame_b64(s.path) for s in samples]
    warnings: list[str] = []
    calls = 0
    situation: Situation | None = None

    if situation_pass:
        request = build_chat_request(
            model,
            SITUATION_SYSTEM_PROMPT,
            build_situation_prompt(window, samples, context),
            images,
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

    system = build_system_prompt(knowledge, situation.phase if situation else None)
    prompt = build_coach_prompt(window, samples, context, situation, knowledge)
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
        return WindowResult(
            window,
            tuple(samples),
            tuple(findings),
            calls,
            tuple(warnings + parse_warnings),
            situation,
            context,
        )

    assert last_error is not None
    raise ParseError(
        f"window {window.index}: model output unparseable after retry: {last_error}",
        model_calls=calls,
    )
