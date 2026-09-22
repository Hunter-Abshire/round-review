"""The session summary is the coach's part: merge symptoms into habits, rank them by what
actually costs rounds, cap the action set, and finish with one thing to practise."""

from pathlib import Path

import pytest

from round_review.coaching.parse import Finding, Strength
from round_review.coaching.review import WindowResult
from round_review.coaching.session import (
    MAX_FOCUS_ITEMS,
    build_session_summary,
    recurrence_points,
)
from round_review.video.windows import Window


def window(index: int) -> Window:
    return Window(index, index * 12.0, index * 12.0 + 12.0, "tiled")


def finding(
    check_id: str, category: str, at: float, confidence: float = 0.8, later: str = ""
) -> Finding:
    return Finding(
        timestamp_s=at,
        check_id=check_id,
        category=category,
        observation=f"observation for {check_id}",
        visible_evidence="visible evidence",
        information_available_to_player="what they knew",
        information_revealed_later=later,
        assumption_flags=(),
        suggested_alternative=f"alternative for {check_id}",
        confidence=confidence,
        evidence_frame=Path(f"/f/{check_id}.jpg"),
    )


def strength(check_id: str, category: str, at: float) -> Strength:
    return Strength(
        timestamp_s=at,
        check_id=check_id,
        category=category,
        observation=f"did {check_id} well, specifically and at length",
        visible_evidence="evidence",
        why_it_worked="it denied the angle",
        confidence=0.8,
        evidence_frame=None,
    )


def result(
    index: int, findings: list[Finding] | None = None, strengths: list[Strength] | None = None
) -> WindowResult:
    return WindowResult(
        window=window(index),
        samples=(),
        findings=tuple(findings or []),
        strengths=tuple(strengths or []),
        model_calls=2,
        warnings=(),
    )


class TestRecurrence:
    @pytest.mark.parametrize(
        ("count", "confidence", "points"),
        [(4, 0.5, 3), (7, 0.5, 3), (3, 0.5, 2), (2, 0.5, 2), (1, 0.9, 1), (1, 0.4, 0)],
    )
    def test_scores_repetition_on_the_research_scale(
        self, count: int, confidence: float, points: int
    ) -> None:
        assert recurrence_points(count, confidence) == points


class TestMerging:
    def test_repeats_of_one_check_become_a_single_habit(self) -> None:
        results = [
            result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(4)
        ]
        summary = build_session_summary(results, rank=None)
        (habit,) = summary.focus
        assert habit.check_id == "peeking.no_repeek"
        assert habit.count == 4
        assert habit.windows == (0, 1, 2, 3)
        assert len(habit.instances) == 4
        # the clearest instance leads, so the player has one moment to look at
        assert habit.instances[0].timestamp_s == 3.0

    def test_different_checks_stay_separate(self) -> None:
        results = [
            result(0, [finding("peeking.no_repeek", "peeking", 3.0)]),
            result(1, [finding("crosshair.head_level", "crosshair", 15.0)]),
        ]
        summary = build_session_summary(results, rank=None)
        assert {h.check_id for h in summary.focus} == {"peeking.no_repeek", "crosshair.head_level"}

    def test_the_action_set_is_capped(self) -> None:
        results = [
            result(i, [finding(f"cat{i}.check", "positioning", i * 12.0 + 1)]) for i in range(8)
        ]
        summary = build_session_summary(results, rank=None)
        assert len(summary.focus) == MAX_FOCUS_ITEMS
        assert len(summary.also_seen) == 8 - MAX_FOCUS_ITEMS


class TestRanking:
    def test_a_recurring_habit_outranks_a_one_off(self) -> None:
        results = [
            result(0, [finding("crosshair.head_level", "crosshair", 1.0)]),
            result(1, [finding("crosshair.head_level", "crosshair", 13.0)]),
            result(2, [finding("crosshair.head_level", "crosshair", 25.0)]),
            result(3, [finding("positioning.cover", "positioning", 37.0)]),
        ]
        summary = build_session_summary(results, rank=None)
        assert summary.focus[0].check_id == "crosshair.head_level"

    def test_a_round_losing_category_outranks_a_cosmetic_one_at_equal_counts(self) -> None:
        results = [
            result(0, [finding("economy.buy_with_team", "economy", 1.0)]),
            result(1, [finding("positioning.cover", "positioning", 13.0)]),
        ]
        summary = build_session_summary(results, rank=None)
        assert summary.focus[0].category == "positioning"

    def test_ranking_is_deterministic_for_identical_scores(self) -> None:
        results = [
            result(0, [finding("positioning.b_check", "positioning", 1.0)]),
            result(1, [finding("positioning.a_check", "positioning", 13.0)]),
        ]
        first = build_session_summary(results, rank=None)
        second = build_session_summary(results, rank=None)
        assert [h.check_id for h in first.focus] == [h.check_id for h in second.focus]
        assert first.focus[0].check_id == "positioning.a_check"  # alphabetical tiebreak


class TestPractice:
    def test_the_summary_ends_with_one_rule_and_one_drill(self) -> None:
        results = [
            result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(3)
        ]
        summary = build_session_summary(results, rank=None)
        assert summary.practice is not None
        assert summary.practice.rule.endswith(".")
        assert "minute" in summary.practice.drill.lower()
        assert summary.practice.for_check_id == "peeking.no_repeek"
        assert summary.practice.success_check

    def test_no_findings_means_no_practice_item(self) -> None:
        summary = build_session_summary([result(0)], rank=None)
        assert summary.practice is None
        assert summary.focus == ()


class TestStrengthsAndHindsight:
    def test_strengths_are_collected_and_capped(self) -> None:
        results = [
            result(i, strengths=[strength("utility.has_purpose", "utility", i * 12.0 + 2)])
            for i in range(5)
        ]
        summary = build_session_summary(results, rank=None)
        assert 1 <= len(summary.strengths) <= 3

    def test_findings_that_lean_on_hindsight_are_separated_out(self) -> None:
        results = [
            result(
                0, [finding("positioning.cover", "positioning", 1.0, later="An enemy was there.")]
            ),
            result(1, [finding("crosshair.head_level", "crosshair", 13.0)]),
        ]
        summary = build_session_summary(results, rank=None)
        assert [h.check_id for h in summary.hindsight] == ["positioning.cover"]
        # and it does not compete for a focus slot
        assert [h.check_id for h in summary.focus] == ["crosshair.head_level"]


class TestRankNote:
    def test_names_what_this_rank_should_be_optimising(self) -> None:
        results = [result(0, [finding("crosshair.head_level", "crosshair", 1.0)])]
        assert build_session_summary(results, rank="Gold 2").rank_focus is not None
        assert build_session_summary(results, rank=None).rank_focus is None


class TestVerdict:
    def test_one_sentence_naming_the_habit_to_fix(self) -> None:
        results = [
            result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(4)
        ]
        verdict = build_session_summary(results, rank=None).verdict
        assert "4 times" in verdict
        assert verdict.endswith(".")

    def test_says_so_plainly_when_there_is_nothing_to_report(self) -> None:
        verdict = build_session_summary([result(0)], rank=None).verdict
        assert "no" in verdict.lower()


class TestFirstSentence:
    def test_takes_the_first_sentence(self) -> None:
        from round_review.coaching.session import first_sentence

        assert (
            first_sentence("You re-peeked the angle. Then you died.") == "You re-peeked the angle"
        )

    def test_truncates_on_a_word_boundary(self) -> None:
        from round_review.coaching.session import first_sentence

        out = first_sentence("word " * 40, limit=30)
        assert len(out) <= 30
        assert out.endswith("…")
        assert "  " not in out

    def test_leaves_a_short_sentence_alone(self) -> None:
        from round_review.coaching.session import first_sentence

        assert first_sentence("Held a wide angle") == "Held a wide angle"
