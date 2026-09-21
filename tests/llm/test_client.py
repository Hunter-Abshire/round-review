from collections import deque

import pytest

from round_review.errors import CapExceeded
from round_review.llm.client import build_chat_request, send_review
from round_review.llm.transport import ChatRequest, ChatResponse


class FakeTransport:
    def __init__(self, *contents: str) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self.responses.popleft(), prompt_eval_count=0, eval_count=0)


def test_build_chat_request() -> None:
    req = build_chat_request(
        model="m",
        system="s",
        prompt="p",
        images_b64=["a", "b"],
        schema={"type": "object"},
        timeout_s=9.0,
    )
    assert req == ChatRequest("m", "s", "p", ("a", "b"), {"type": "object"}, 9.0)


def test_send_review_calls_transport_when_under_cap() -> None:
    transport = FakeTransport("{}")
    req = build_chat_request("m", "s", "p", [], None, 1.0)
    resp = send_review(req, transport, calls_today=29, cap=30)
    assert resp.content == "{}"
    assert transport.calls == [req]


def test_send_review_raises_before_transport_when_at_cap() -> None:
    transport = FakeTransport("{}")
    req = build_chat_request("m", "s", "p", [], None, 1.0)
    with pytest.raises(CapExceeded, match="30"):
        send_review(req, transport, calls_today=30, cap=30)
    assert transport.calls == []


def test_zero_cap_means_unlimited() -> None:
    transport = FakeTransport("{}")
    req = build_chat_request("m", "s", "p", [], None, 1.0)
    assert send_review(req, transport, calls_today=9999, cap=0).content == "{}"
