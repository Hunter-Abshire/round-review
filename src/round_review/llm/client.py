"""Request construction and the daily-cap gate in front of the transport."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from round_review.errors import CapExceeded
from round_review.llm.transport import ChatRequest, ChatResponse, Transport


def build_chat_request(
    model: str,
    system: str,
    prompt: str,
    images_b64: Sequence[str],
    schema: dict[str, Any] | str | None,
    timeout_s: float,
) -> ChatRequest:
    return ChatRequest(model, system, prompt, tuple(images_b64), schema, timeout_s)


def send_review(
    request: ChatRequest, transport: Transport, calls_today: int, cap: int
) -> ChatResponse:
    """Call the model unless the daily cap is already reached. The cap check runs first
    so a capped day never touches the network. A cap of 0 means unlimited: local inference
    has no per-call cost, and a full-video review needs dozens of calls."""
    if cap > 0 and calls_today >= cap:
        raise CapExceeded(f"daily model-call cap reached ({calls_today}/{cap})")
    return transport.chat(request)
