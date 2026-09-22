"""Thumbs on a finding: the only way to know whether any of this is actually right."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from round_review.feedback import (
    FeedbackEntry,
    dismissal_rate,
    hit_rate,
    read_feedback,
    record_feedback,
)

WHEN = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def entry(check_id: str, verdict: str, key: str = "abc123") -> FeedbackEntry:
    return FeedbackEntry(
        key=key, check_id=check_id, timestamp_s=12.0, verdict=verdict, noted_at=WHEN
    )


class TestPersistence:
    def test_records_append_and_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / "feedback.jsonl"
        record_feedback(path, entry("positioning.one_line", "useful"))
        record_feedback(path, entry("positioning.one_line", "wrong"))
        got = read_feedback(path)
        assert [e.verdict for e in got] == ["useful", "wrong"]

    def test_changing_your_mind_replaces_the_verdict(self, tmp_path: Path) -> None:
        path = tmp_path / "feedback.jsonl"
        record_feedback(path, entry("positioning.one_line", "useful"))
        record_feedback(path, entry("positioning.one_line", "wrong"))
        # same clip, same check, same moment: one opinion, the latest one
        assert len(read_feedback(path)) == 2
        assert read_feedback(path, latest_only=True)[0].verdict == "wrong"

    def test_a_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert read_feedback(tmp_path / "nope.jsonl") == ()

    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "feedback.jsonl"
        record_feedback(path, entry("a.b", "useful"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        assert len(read_feedback(path)) == 1

    def test_an_unknown_verdict_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="verdict"):
            record_feedback(tmp_path / "f.jsonl", entry("a.b", "maybe"))


class TestRates:
    def test_hit_rate_is_useful_over_rated(self) -> None:
        entries = (entry("a.b", "useful"), entry("a.b", "wrong"), entry("c.d", "useful"))
        assert hit_rate(entries) == pytest.approx(2 / 3)

    def test_no_feedback_has_no_hit_rate(self) -> None:
        assert hit_rate(()) is None

    def test_dismissal_rate_is_per_check(self) -> None:
        entries = (
            entry("a.b", "wrong"),
            entry("a.b", "wrong"),
            entry("a.b", "useful"),
            entry("c.d", "useful"),
        )
        rates = dismissal_rate(entries)
        assert rates["a.b"] == pytest.approx(2 / 3)
        assert rates["c.d"] == pytest.approx(0.0)

    def test_a_check_with_no_feedback_is_absent(self) -> None:
        assert "z.z" not in dismissal_rate((entry("a.b", "useful"),))
