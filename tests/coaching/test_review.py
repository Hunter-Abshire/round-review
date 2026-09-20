import json
from collections import deque
from pathlib import Path

import pytest

from round_review.coaching.review import WindowResult, review_window
from round_review.errors import CapExceeded, ParseError
from round_review.llm.transport import ChatRequest, ChatResponse
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(index=0, start_s=60.0, end_s=72.0, source="evenly_spaced")


class FakeTransport:
    def __init__(self, *contents: str) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self.responses.popleft(), prompt_eval_count=0, eval_count=0)


GOOD = json.dumps(
    {
        "findings": [
            {
                "timestamp_s": 61.0,
                "category": "utility",
                "observation": "o",
                "visible_evidence": "v",
                "information_available_to_player": "i",
                "information_revealed_later": "",
                "assumption_flags": [],
                "suggested_alternative": "s",
                "confidence": 0.6,
            }
        ]
    }
)


@pytest.fixture
def samples(tmp_path: Path) -> list[FrameSample]:
    out = []
    for i in range(3):
        p = tmp_path / f"w00_{i:03d}.jpg"
        p.write_bytes(b"jpg")
        out.append(FrameSample(0, 60.0 + i, p))
    return out


def test_happy_path_one_call(samples: list[FrameSample]) -> None:
    transport = FakeTransport(GOOD)
    result = review_window(
        WINDOW, samples, transport, model="m", calls_today=0, cap=10, timeout_s=1.0, context=None
    )
    assert isinstance(result, WindowResult)
    assert result.model_calls == 1
    assert len(result.findings) == 1
    assert result.warnings == ()
    assert len(transport.calls[0].images_b64) == 3
    assert transport.calls[0].model == "m"


def test_retries_once_with_nudge_then_succeeds(samples: list[FrameSample]) -> None:
    transport = FakeTransport("I cannot help with that.", GOOD)
    result = review_window(
        WINDOW, samples, transport, model="m", calls_today=0, cap=10, timeout_s=1.0, context=None
    )
    assert result.model_calls == 2
    assert len(result.findings) == 1
    assert "json" in transport.calls[1].prompt.lower()
    assert any("retry" in w.lower() for w in result.warnings)


def test_second_failure_raises_parse_error(samples: list[FrameSample]) -> None:
    transport = FakeTransport("nope", "still nope")
    with pytest.raises(ParseError):
        review_window(
            WINDOW,
            samples,
            transport,
            model="m",
            calls_today=0,
            cap=10,
            timeout_s=1.0,
            context=None,
        )
    assert len(transport.calls) == 2


def test_retry_respects_cap(samples: list[FrameSample]) -> None:
    transport = FakeTransport("nope", GOOD)
    with pytest.raises(CapExceeded):
        review_window(
            WINDOW,
            samples,
            transport,
            model="m",
            calls_today=9,
            cap=10,
            timeout_s=1.0,
            context=None,
        )
    assert len(transport.calls) == 1
