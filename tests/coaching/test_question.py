"""Asking the coach about one specific stretch of the recording."""

import json
from pathlib import Path
from typing import Any

import pytest

from round_review.coaching.context import PlayerContext
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.question import (
    ANSWER_SCHEMA,
    MAX_ALTERNATIVES,
    Answer,
    QuestionSpec,
    build_question_prompt,
    build_question_system_prompt,
    clamp_span,
    parse_answer,
)
from round_review.coaching.situation import Situation
from round_review.errors import ParseError
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(index=0, start_s=100.0, end_s=112.0, source="asked")
SAMPLES = [FrameSample(0, 100.0 + i, Path(f"/f/q_{i:03d}.jpg")) for i in range(6)]


def raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "answerable": True,
        "answer": "You had no reason to push that angle alone with your team still in spawn.",
        "what_you_could_see": "The minimap at t=104.0s shows three teammates behind you.",
        "what_you_could_not_know": "",
        "assumptions": ["enemy position"],
        "alternatives": [
            {
                "action": "Hold the corner and wait for your team to arrive.",
                "why": "It keeps the trade available.",
            },
            {
                "action": "Use the smoke first, then take the same angle.",
                "why": "It removes the far sightline.",
            },
        ],
        "confidence": 0.8,
    }
    return {**base, **overrides}


class TestClampSpan:
    def test_keeps_a_sensible_range(self) -> None:
        assert clamp_span(100.0, 112.0, duration_s=600.0, max_span_s=60.0) == (100.0, 112.0)

    def test_truncates_an_over_long_range_from_the_start(self) -> None:
        assert clamp_span(10.0, 300.0, duration_s=600.0, max_span_s=60.0) == (10.0, 70.0)

    def test_clamps_to_the_recording(self) -> None:
        assert clamp_span(-5.0, 20.0, duration_s=600.0, max_span_s=60.0) == (0.0, 20.0)
        assert clamp_span(590.0, 700.0, duration_s=600.0, max_span_s=60.0) == (590.0, 600.0)

    def test_a_backwards_or_zero_range_becomes_a_moment_around_the_start(self) -> None:
        assert clamp_span(100.0, 100.0, duration_s=600.0, max_span_s=60.0) == (96.0, 104.0)
        assert clamp_span(100.0, 90.0, duration_s=600.0, max_span_s=60.0) == (96.0, 104.0)

    def test_a_click_at_the_very_end_shifts_back_instead_of_shrinking(self) -> None:
        assert clamp_span(600.0, 600.0, duration_s=600.0, max_span_s=60.0) == (592.0, 600.0)

    def test_a_moment_near_the_start_does_not_go_negative(self) -> None:
        assert clamp_span(1.0, 1.0, duration_s=600.0, max_span_s=60.0) == (0.0, 8.0)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_a_recording_with_no_duration_is_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError):
            clamp_span(0.0, 10.0, duration_s=bad, max_span_s=60.0)


class TestPrompt:
    def test_the_schema_requires_evidence_and_alternatives(self) -> None:
        json.dumps(ANSWER_SCHEMA)
        for key in (
            "answerable",
            "answer",
            "what_you_could_see",
            "what_you_could_not_know",
            "assumptions",
            "alternatives",
            "confidence",
        ):
            assert key in ANSWER_SCHEMA["required"], key
        assert ANSWER_SCHEMA["properties"]["alternatives"]["maxItems"] == MAX_ALTERNATIVES

    def test_the_system_prompt_forbids_hindsight_and_demands_options(self) -> None:
        text = build_question_system_prompt(load_knowledge(), phase=None).lower()
        assert "later" in text and "earlier" in text
        assert "option" in text or "alternative" in text
        assert "say so" in text  # it must be able to decline

    def test_the_question_is_quoted_verbatim(self) -> None:
        spec = QuestionSpec(start_s=100.0, end_s=112.0, question="How could I have used util here?")
        text = build_question_prompt(WINDOW, SAMPLES, spec, PlayerContext(), None, load_knowledge())
        assert "How could I have used util here?" in text
        assert "1:40" in text and "1:52" in text  # the range in clock terms
        assert "6 frames" in text

    def test_the_agent_and_map_briefs_are_included_when_known(self) -> None:
        spec = QuestionSpec(100.0, 112.0, "What should I have done?")
        context = PlayerContext(rank="Gold 2", agent="Jett", map="Ascent")
        text = build_question_prompt(WINDOW, SAMPLES, spec, context, None, load_knowledge())
        assert "Agent brief: Jett" in text
        assert "Map brief: Ascent" in text
        assert "Gold 2" in text

    def test_the_situation_read_is_included_when_available(self) -> None:
        situation = Situation(
            "Jett",
            "Ascent",
            "attack",
            "mid",
            "Vandal",
            ("Tailwind",),
            3900,
            4,
            1,
            ((100.0, "walks A main"),),
            "Mid round on A.",
        )
        text = build_question_prompt(
            WINDOW,
            SAMPLES,
            QuestionSpec(100.0, 112.0, "q"),
            PlayerContext(),
            situation,
            load_knowledge(),
        )
        assert "Mid round on A." in text


class TestParseAnswer:
    def test_parses_an_answer(self) -> None:
        answer = parse_answer(json.dumps(raw()), QuestionSpec(100.0, 112.0, "q"))
        assert isinstance(answer, Answer)
        assert answer.answerable is True
        assert answer.answer.startswith("You had no reason")
        assert len(answer.alternatives) == 2
        assert answer.alternatives[0].action.startswith("Hold the corner")
        assert answer.alternatives[0].why
        assert answer.assumptions == ("enemy position",)
        assert answer.confidence == 0.8
        assert answer.warnings == ()

    def test_tolerates_prose_around_the_json(self) -> None:
        text = "Sure!\n```json\n" + json.dumps(raw()) + "\n```\n"
        assert parse_answer(text, QuestionSpec(100.0, 112.0, "q")).answerable is True

    def test_an_unanswerable_question_is_kept_not_discarded(self) -> None:
        answer = parse_answer(
            json.dumps(
                raw(
                    answerable=False,
                    answer="The frames do not show your abilities.",
                    alternatives=[],
                    confidence=0.2,
                )
            ),
            QuestionSpec(100.0, 112.0, "q"),
        )
        assert answer.answerable is False
        assert answer.alternatives == ()

    def test_too_many_alternatives_are_truncated(self) -> None:
        many = [{"action": f"option {i}", "why": "because"} for i in range(6)]
        answer = parse_answer(json.dumps(raw(alternatives=many)), QuestionSpec(100.0, 112.0, "q"))
        assert len(answer.alternatives) == MAX_ALTERNATIVES
        assert any("truncated" in w for w in answer.warnings)

    def test_a_malformed_alternative_is_dropped_with_a_warning(self) -> None:
        answer = parse_answer(
            json.dumps(raw(alternatives=[{"action": "no why given"}, {"action": "a", "why": "b"}])),
            QuestionSpec(100.0, 112.0, "q"),
        )
        assert len(answer.alternatives) == 1
        assert any("why" in w for w in answer.warnings)

    def test_leaning_on_hindsight_lowers_the_confidence(self) -> None:
        answer = parse_answer(
            json.dumps(raw(what_you_could_not_know="An enemy was behind the box.", confidence=0.9)),
            QuestionSpec(100.0, 112.0, "q"),
        )
        assert answer.confidence == 0.5
        assert any("later" in w for w in answer.warnings)

    def test_confidence_is_clamped(self) -> None:
        assert (
            parse_answer(json.dumps(raw(confidence=3.0)), QuestionSpec(1.0, 2.0, "q")).confidence
            == 1.0
        )

    def test_missing_a_required_field_is_a_parse_error(self) -> None:
        bad = raw()
        del bad["what_you_could_see"]
        with pytest.raises(ParseError, match="what_you_could_see"):
            parse_answer(json.dumps(bad), QuestionSpec(100.0, 112.0, "q"))

    def test_garbage_is_a_parse_error(self) -> None:
        with pytest.raises(ParseError):
            parse_answer("I think you played fine", QuestionSpec(100.0, 112.0, "q"))
