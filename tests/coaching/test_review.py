import json
from collections import deque
from pathlib import Path

import pytest

from round_review.coaching.context import PlayerContext
from round_review.coaching.knowledge import load_knowledge
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


SITUATION = json.dumps(
    {
        "agent": "Jett",
        "map": "Ascent",
        "side": "attack",
        "phase": "early",
        "weapon": "Vandal",
        "abilities_available": ["Tailwind"],
        "credits": 3900,
        "teammates_alive": 4,
        "enemies_visible": 0,
        "timeline": [{"t": 60.0, "event": "walking A main"}],
        "summary": "Entering A main with dash up.",
    }
)
KNOWLEDGE = load_knowledge()


def review(
    transport: FakeTransport, samples: list[FrameSample], **overrides: object
) -> WindowResult:
    kwargs: dict[str, object] = {
        "model": "m",
        "calls_today": 0,
        "cap": 10,
        "timeout_s": 1.0,
        "context": PlayerContext(rank="Gold 2"),
        "knowledge": KNOWLEDGE,
        "situation_pass": True,
    }
    kwargs.update(overrides)
    return review_window(WINDOW, samples, transport, **kwargs)  # type: ignore[arg-type]


GOOD = json.dumps(
    {
        "findings": [
            {
                "timestamp_s": 61.0,
                "check_id": "utility.has_purpose",
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


def test_two_pass_happy_path(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    result = review(transport, samples)
    assert isinstance(result, WindowResult)
    assert result.model_calls == 2
    assert len(result.findings) == 1
    assert result.findings[0].check_id == "utility.has_purpose"
    assert result.warnings == ()
    assert result.situation is not None and result.situation.agent == "Jett"
    # detected agent/map merged into the context the coach pass saw; user rank kept
    assert result.context == PlayerContext(rank="Gold 2", agent="Jett", map="Ascent", side="attack")
    assert len(transport.calls[0].images_b64) == 3
    assert "Agent brief: Jett" in transport.calls[1].prompt
    assert "Map brief: Ascent" in transport.calls[1].prompt
    assert "Entering A main with dash up." in transport.calls[1].prompt
    assert transport.calls[1].model == "m"


def test_situation_pass_can_be_disabled(samples: list[FrameSample]) -> None:
    transport = FakeTransport(GOOD)
    result = review(transport, samples, situation_pass=False)
    assert result.model_calls == 1
    assert result.situation is None
    assert result.context == PlayerContext(rank="Gold 2")


def test_unparseable_situation_is_a_warning_not_a_failure(samples: list[FrameSample]) -> None:
    transport = FakeTransport("I see a video game.", GOOD)
    result = review(transport, samples)
    assert result.model_calls == 2
    assert result.situation is None
    assert len(result.findings) == 1
    assert any("situation" in w.lower() for w in result.warnings)


def test_retries_coach_pass_once_with_nudge_then_succeeds(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, "I cannot help with that.", GOOD)
    result = review(transport, samples)
    assert result.model_calls == 3
    assert len(result.findings) == 1
    assert "json" in transport.calls[2].prompt.lower()
    assert any("retry" in w.lower() for w in result.warnings)


def test_second_coach_failure_raises_parse_error_with_calls(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, "nope", "still nope")
    with pytest.raises(ParseError) as info:
        review(transport, samples)
    assert info.value.model_calls == 3
    assert len(transport.calls) == 3


def test_cap_is_enforced_across_passes(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    with pytest.raises(CapExceeded):
        review(transport, samples, calls_today=9, cap=10)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("phase", ["pre_round", "spectating", "unknown"])
def test_noncombat_or_unreadable_phase_abstains_without_coach_call(samples, phase) -> None:
    situation = {**json.loads(SITUATION), "phase": phase, "weapon": "Knife"}
    transport = FakeTransport(json.dumps(situation))
    result = review(transport, samples)
    assert result.findings == ()
    assert result.model_calls == 1
    assert result.warnings and "coaching skipped" in result.warnings[0]
    if phase != "unknown":
        assert result.situation is not None and result.situation.phase == phase


@pytest.mark.parametrize("weapon", ["Knife", "Melee", "unknown"])
def test_crosshair_findings_require_a_known_firearm(samples, weapon) -> None:
    situation = {**json.loads(SITUATION), "weapon": weapon}
    aim = {
        **json.loads(GOOD)["findings"][0],
        "check_id": "crosshair.head_level",
        "category": "crosshair",
    }
    transport = FakeTransport(json.dumps(situation), json.dumps({"findings": [aim]}))
    result = review(transport, samples)
    assert result.findings == ()
    assert any("crosshair" in w for w in result.warnings)


def test_phase_inappropriate_check_is_rejected_even_if_category_is_mislabeled(samples) -> None:
    finding = {
        **json.loads(GOOD)["findings"][0],
        "check_id": "postplant.spike_vision",
        "category": "utility",
    }
    result = review(FakeTransport(SITUATION, json.dumps({"findings": [finding]})), samples)
    assert result.findings == ()
    assert any("phase" in w for w in result.warnings)


def test_abstention_is_not_a_parse_failure(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    transport = FakeTransport(json.dumps(buy_phase))
    result = review(transport, samples)
    assert result.findings == ()
    assert result.parse_failed is False
    assert any("coaching skipped" in w for w in result.warnings)
    assert len(transport.calls) == 1  # no coach call spent on buy phase


def test_unparseable_coach_output_sets_parse_failed_on_the_raised_error(
    samples: list[FrameSample],
) -> None:
    transport = FakeTransport(SITUATION, "nope", "still nope")
    with pytest.raises(ParseError) as info:
        review(transport, samples)
    assert info.value.model_calls == 3


def test_successful_review_is_not_marked_parse_failed(samples: list[FrameSample]) -> None:
    result = review(FakeTransport(SITUATION, GOOD), samples)
    assert result.parse_failed is False
