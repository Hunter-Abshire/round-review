"""The report has to read like a coach's write-up: verdict, corrections, then praise,
then one thing to practise."""

from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.session import build_session_summary
from round_review.report.markdown import Report, render_report
from round_review.video.probe import Recording
from tests.coaching.test_session import finding, result, strength


def build(results: list[object], rank: str | None = "Gold 2") -> str:
    recording = Recording(Path("/vids/match.mp4"), 600.0, 60.0, 1920, 1080, 1, 0.0)
    report = Report(
        recording=recording,
        generated_at=datetime(2026, 9, 21, 15, 30, tzinfo=UTC),
        model="qwen3-vl:8b",
        results=tuple(results),  # type: ignore[arg-type]
        warnings=(),
        summary=build_session_summary(results, rank=rank),  # type: ignore[arg-type]
    )
    return render_report(report, base_dir=Path("/tmp"))


def test_the_verdict_comes_first_and_only_once() -> None:
    text = build(
        [result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(4)]
    )
    assert "## The one thing to fix" in text
    assert text.index("The one thing to fix") < text.index("## What to work on")
    assert text.count("4 times") >= 1


def test_corrections_come_before_praise() -> None:
    results = [
        result(
            0,
            [finding("peeking.no_repeek", "peeking", 3.0)],
            [strength("utility.has_purpose", "utility", 5.0)],
        ),
    ]
    text = build(results)
    assert "## What to work on" in text
    assert "## What worked" in text
    # research: corrective first, genuine praise after, never as a cushion
    assert text.index("## What to work on") < text.index("## What worked")


def test_each_focus_item_is_numbered_with_its_category_and_count() -> None:
    results = [result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(3)]
    text = build(results)
    assert "### 1." in text
    assert "peeking" in text
    assert "3 times" in text
    assert "Try instead" in text
    assert "0:03" in text  # a timestamp to go and look at


def test_findings_are_grouped_by_category_not_scattered_by_window() -> None:
    results = [
        result(0, [finding("crosshair.head_level", "crosshair", 1.0)]),
        result(1, [finding("peeking.no_repeek", "peeking", 13.0)]),
        result(2, [finding("crosshair.corner_edge", "crosshair", 25.0)]),
    ]
    text = build(results)
    assert "## Everything else seen" in text or "crosshair" in text
    # both crosshair checks appear under one heading, not once per window
    assert text.count("## Window") == 0


def test_praise_is_omitted_entirely_when_there_is_none() -> None:
    text = build([result(0, [finding("peeking.no_repeek", "peeking", 3.0)])])
    assert "## What worked" not in text


def test_the_report_ends_with_one_practice_item() -> None:
    text = build(
        [result(i, [finding("peeking.no_repeek", "peeking", i * 12.0 + 3)]) for i in range(3)]
    )
    assert "## Before your next game" in text
    assert "In-game rule" in text
    assert "Drill" in text
    assert "how you will know" in text.lower()


def test_hindsight_findings_are_flagged_separately() -> None:
    results = [
        result(
            0, [finding("positioning.cover", "positioning", 1.0, later="An enemy was behind it.")]
        ),
    ]
    text = build(results)
    assert "hindsight" in text.lower()
    assert "An enemy was behind it." in text


def test_the_rank_band_note_appears() -> None:
    text = build([result(0, [finding("peeking.no_repeek", "peeking", 3.0)])], rank="Gold 2")
    assert "At your rank" in text
    assert "At your rank" not in build(
        [result(0, [finding("peeking.no_repeek", "peeking", 3.0)])], rank=None
    )


def test_the_report_states_what_it_could_not_see() -> None:
    text = build([result(0, [finding("peeking.no_repeek", "peeking", 3.0)])])
    lower = text.lower()
    assert "sampled" in lower
    assert "round" in lower  # names that per-round outcome data was not available


def test_an_empty_review_says_so_without_inventing_advice() -> None:
    text = build([result(0)])
    assert "Nothing worth acting on" in text
    assert "## What to work on" not in text
    assert "## Before your next game" not in text
