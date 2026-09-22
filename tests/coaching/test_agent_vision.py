"""Asking the model one narrow question about four icons, instead of guessing per window."""

import json
from collections import deque

import pytest

from round_review.coaching.agent_vision import (
    AGENT_SCHEMA,
    build_agent_prompt,
    identify_agent_with_model,
)
from round_review.coaching.knowledge import load_knowledge
from round_review.errors import ParseError
from round_review.llm.transport import ChatRequest, ChatResponse

KNOWLEDGE = load_knowledge()
NAMES = tuple(sorted(a.name for a in KNOWLEDGE.agents.values()))


class FakeTransport:
    def __init__(self, *contents: str) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self.responses.popleft(), prompt_eval_count=0, eval_count=0)


class TestPrompt:
    def test_lists_every_agent_so_the_answer_is_a_choice_not_a_memory(self) -> None:
        text = build_agent_prompt(NAMES)
        assert "Veto" in text and "Omen" in text
        assert "unknown" in text.lower()

    def test_the_schema_constrains_the_answer_to_a_name(self) -> None:
        assert set(AGENT_SCHEMA["required"]) == {"agent", "confidence"}


class TestIdentify:
    def test_a_confident_named_agent_is_returned(self) -> None:
        reply = json.dumps({"agent": "Veto", "confidence": 0.9})
        got = identify_agent_with_model(FakeTransport(reply), "m", ["b64"], NAMES, 10.0, 0.6)
        assert got == ("Veto", 0.9)

    def test_an_agent_that_does_not_exist_is_refused(self) -> None:
        reply = json.dumps({"agent": "Liam", "confidence": 0.95})
        assert identify_agent_with_model(FakeTransport(reply), "m", ["b64"], NAMES, 10.0, 0.6) == (
            None,
            0.0,
        )

    def test_low_confidence_is_refused(self) -> None:
        reply = json.dumps({"agent": "Veto", "confidence": 0.2})
        assert identify_agent_with_model(FakeTransport(reply), "m", ["b64"], NAMES, 10.0, 0.6) == (
            None,
            0.0,
        )

    def test_an_explicit_unknown_is_respected(self) -> None:
        reply = json.dumps({"agent": "unknown", "confidence": 0.9})
        assert identify_agent_with_model(FakeTransport(reply), "m", ["b64"], NAMES, 10.0, 0.6) == (
            None,
            0.0,
        )

    def test_the_name_is_matched_loosely(self) -> None:
        reply = json.dumps({"agent": "kay/o", "confidence": 0.9})
        name, _ = identify_agent_with_model(FakeTransport(reply), "m", ["b64"], NAMES, 10.0, 0.6)
        assert name == "KAY/O"

    def test_unparseable_replies_raise(self) -> None:
        with pytest.raises(ParseError):
            identify_agent_with_model(FakeTransport("nope"), "m", ["b64"], NAMES, 10.0, 0.6)

    def test_no_images_means_no_call(self) -> None:
        transport = FakeTransport()
        assert identify_agent_with_model(transport, "m", [], NAMES, 10.0, 0.6) == (None, 0.0)
        assert transport.calls == []
