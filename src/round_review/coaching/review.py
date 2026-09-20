"""Review one window: frames -> model -> findings, with a single retry on unparseable output."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from round_review.coaching.parse import Finding, parse_findings
from round_review.coaching.prompt import (
    FINDING_SCHEMA,
    RETRY_NUDGE,
    SYSTEM_PROMPT,
    build_user_prompt,
)
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


def review_window(
    window: Window,
    samples: Sequence[FrameSample],
    transport: Transport,
    model: str,
    calls_today: int,
    cap: int,
    timeout_s: float,
    context: str | None,
) -> WindowResult:
    """Ask the model about one window. Retries once with a JSON-only nudge if the first reply
    cannot be parsed; a second failure raises ParseError. Every call counts against the cap."""
    images = [encode_frame_b64(s.path) for s in samples]
    prompt = build_user_prompt(window, samples, context)
    warnings: list[str] = []
    calls = 0
    last_error: ParseError | None = None

    for attempt in range(2):
        text = prompt if attempt == 0 else prompt + RETRY_NUDGE
        request = build_chat_request(model, SYSTEM_PROMPT, text, images, FINDING_SCHEMA, timeout_s)
        response = send_review(request, transport, calls_today + calls, cap)
        calls += 1
        try:
            findings, parse_warnings = parse_findings(response.content, window, samples)
        except ParseError as exc:
            last_error = exc
            log.warning("window %d attempt %d unparseable: %s", window.index, attempt + 1, exc)
            warnings.append(f"attempt {attempt + 1} unparseable, retry issued: {exc}")
            continue
        return WindowResult(
            window, tuple(samples), tuple(findings), calls, tuple(warnings + parse_warnings)
        )

    assert last_error is not None
    raise ParseError(
        f"window {window.index}: model output unparseable after retry: {last_error}",
        model_calls=calls,
    )
