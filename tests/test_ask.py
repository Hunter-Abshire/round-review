"""Answering a question about one stretch of a recording, end to end with fakes."""

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pytest

from round_review.coaching.context import PlayerContext
from round_review.coaching.question import QuestionSpec
from round_review.config import Config
from round_review.errors import CapExceeded, OllamaError, ParseError
from round_review.llm.transport import ChatRequest, ChatResponse
from round_review.pipeline import Deps, answer_question
from tests.coaching.test_review import SITUATION
from tests.video.test_probe import PROBE_JSON

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

ANSWER = json.dumps(
    {
        "answerable": True,
        "answer": "You pushed that angle alone with your team still crossing.",
        "what_you_could_see": "The minimap shows three teammates behind you.",
        "what_you_could_not_know": "",
        "assumptions": ["enemy position"],
        "alternatives": [
            {"action": "Hold the corner until your team arrives.", "why": "Keeps the trade."},
            {"action": "Smoke first, then take the angle.", "why": "Removes the far sightline."},
        ],
        "confidence": 0.8,
    }
)


class FakeProbe:
    def run(self, args: list[str]) -> str:
        return PROBE_JSON  # duration 123.456


class FakeFfmpeg:
    def __init__(self, frames: int = 4) -> None:
        self.frames = frames
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        pattern = Path(args[-1])
        pattern.parent.mkdir(parents=True, exist_ok=True)
        if "%" not in pattern.name:
            pattern.write_bytes(b"jpg")
            return ""
        for i in range(1, self.frames + 1):
            (pattern.parent / (pattern.name % i)).write_bytes(b"jpg")
        return ""


class FakeTransport:
    def __init__(self, *contents: str | Exception) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        item = self.responses.popleft()
        if isinstance(item, Exception):
            raise item
        return ChatResponse(str(item), 0, 0)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "match.mp4"
    path.write_bytes(b"\x00" * 100)
    return path


def make_deps(tmp_path: Path, transport: FakeTransport, **overrides: object) -> Deps:
    settings: dict[str, object] = {
        "reports_dir": tmp_path / "reports",
        "ledger_path": tmp_path / "ledger.jsonl",
        "situation_pass": True,
        "hud_check": False,
    }
    settings.update(overrides)
    return Deps(
        config=Config(**settings),  # type: ignore[arg-type]
        probe_runner=FakeProbe(),
        ffmpeg_runner=overrides.pop("ffmpeg", FakeFfmpeg()),  # type: ignore[arg-type]
        transport=transport,
        clock=lambda: NOW,
    )


def test_answers_a_question_about_a_range(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport)
    answer = answer_question(
        video,
        deps,
        QuestionSpec(40.0, 52.0, "What should I have done?"),
        PlayerContext(rank="Gold 2"),
    )
    assert answer.answerable is True
    assert answer.question == "What should I have done?"
    assert (answer.start_s, answer.end_s) == (40.0, 52.0)
    assert len(answer.alternatives) == 2
    # the situation read informs the answer, and the question is quoted to the model
    assert "What should I have done?" in transport.calls[1].prompt
    assert "Gold 2" in transport.calls[1].prompt


def test_the_range_is_clamped_to_the_recording(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport)
    answer = answer_question(video, deps, QuestionSpec(100.0, 999.0, "q"), PlayerContext())
    assert answer.end_s == pytest.approx(123.456)


def test_a_click_becomes_a_short_window(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(SITUATION, ANSWER)
    answer = answer_question(
        video, make_deps(tmp_path, transport), QuestionSpec(60.0, 60.0, "q"), PlayerContext()
    )
    assert (answer.end_s - answer.start_s) == pytest.approx(8.0)


def test_the_frame_budget_is_respected(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport, question_frames=2, situation_frames=1)
    answer_question(video, deps, QuestionSpec(40.0, 52.0, "q"), PlayerContext())
    assert len(transport.calls[0].images_b64) == 1  # situation pass
    assert len(transport.calls[1].images_b64) == 2  # question pass


def test_the_situation_pass_can_be_skipped(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(ANSWER)
    deps = make_deps(tmp_path, transport, situation_pass=False)
    assert answer_question(video, deps, QuestionSpec(40.0, 52.0, "q"), PlayerContext()).answerable
    assert len(transport.calls) == 1


def test_an_unreadable_situation_does_not_stop_the_answer(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport("not a situation", ANSWER)
    answer = answer_question(
        video, make_deps(tmp_path, transport), QuestionSpec(40.0, 52.0, "q"), PlayerContext()
    )
    assert answer.answerable is True


def test_an_unparseable_answer_is_retried_once_then_raises(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(SITUATION, "nope", ANSWER)
    answer = answer_question(
        video, make_deps(tmp_path, transport), QuestionSpec(40.0, 52.0, "q"), PlayerContext()
    )
    assert answer.answerable is True
    assert len(transport.calls) == 3

    hopeless = FakeTransport(SITUATION, "nope", "still nope")
    with pytest.raises(ParseError):
        answer_question(
            video, make_deps(tmp_path, hopeless), QuestionSpec(40.0, 52.0, "q"), PlayerContext()
        )


def test_transport_and_cap_failures_surface(video: Path, tmp_path: Path) -> None:
    with pytest.raises(OllamaError):
        answer_question(
            video,
            make_deps(tmp_path, FakeTransport(OllamaError("refused"))),
            QuestionSpec(40.0, 52.0, "q"),
            PlayerContext(),
        )
    capped = make_deps(tmp_path, FakeTransport(SITUATION, ANSWER), daily_call_cap=1)
    from round_review.ledger import LedgerEntry, append_entry

    append_entry(
        capped.config.ledger_path,
        LedgerEntry("k", "/v/a.mp4", NOW, 5, None, "ok", None, windows=1, duration_s=1.0),
    )
    with pytest.raises(CapExceeded):
        answer_question(video, capped, QuestionSpec(40.0, 52.0, "q"), PlayerContext())


def test_an_empty_question_is_rejected_before_any_model_call(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError, match="question"):
        answer_question(
            video, make_deps(tmp_path, transport), QuestionSpec(40.0, 52.0, "   "), PlayerContext()
        )
    assert transport.calls == []


def test_notes_and_knowledge_are_retrieved_for_the_question(video: Path, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "lineups.md").write_text(
        "# Viper wall on Bind\nStand on the box in attacker spawn and aim at the third tile.\n"
    )
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport, notes_dir=notes)
    answer = answer_question(
        video,
        deps,
        QuestionSpec(40.0, 52.0, "What is my Viper wall lineup for Bind?"),
        PlayerContext(),
    )
    prompt = transport.calls[1].prompt
    assert "third tile" in prompt  # the player's own note reached the model
    assert answer.sources
    assert any("Viper wall on Bind" in s for s in answer.sources)


def test_retrieval_can_be_switched_off(video: Path, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "n.md").write_text("# Thing\nsomething very distinctive here\n")
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport, notes_dir=notes, reference_passages=0)
    answer = answer_question(video, deps, QuestionSpec(40.0, 52.0, "thing?"), PlayerContext())
    assert "something very distinctive" not in transport.calls[1].prompt
    assert answer.sources == ()


def test_a_bad_notes_folder_does_not_stop_the_answer(video: Path, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "huge.md").write_text("x" * (2 * 1024 * 1024))
    transport = FakeTransport(SITUATION, ANSWER)
    deps = make_deps(tmp_path, transport, notes_dir=notes)
    answer = answer_question(video, deps, QuestionSpec(40.0, 52.0, "why?"), PlayerContext())
    assert answer.answerable is True  # the question is still answered
