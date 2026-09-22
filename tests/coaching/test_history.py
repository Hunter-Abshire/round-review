"""Tagging habits against previous matches: the part that makes it a coach who remembers."""

from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.history import (
    MatchHabits,
    append_match,
    read_history,
    tag_habits,
)

WHEN = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def match(key: str, counts: dict[str, int], windows: int = 20) -> MatchHabits:
    return MatchHabits(key=key, reviewed_at=WHEN, windows=windows, counts=counts)


class TestTagHabits:
    def test_a_check_never_seen_before_is_new(self) -> None:
        tags = tag_habits({"positioning.one_line": 2}, (), windows=20)
        assert tags["positioning.one_line"] == "new"

    def test_a_check_seen_at_the_same_rate_is_a_repeat(self) -> None:
        history = (match("a", {"positioning.one_line": 2}),)
        tags = tag_habits({"positioning.one_line": 2}, history, windows=20)
        assert tags["positioning.one_line"] == "repeat"

    def test_a_much_lower_rate_is_improving(self) -> None:
        history = (match("a", {"positioning.one_line": 8}),)
        tags = tag_habits({"positioning.one_line": 2}, history, windows=20)
        assert tags["positioning.one_line"] == "improving"

    def test_rates_are_compared_not_raw_counts(self) -> None:
        # Same rate, half the match length: that is not an improvement.
        history = (match("a", {"positioning.one_line": 8}, windows=40),)
        tags = tag_habits({"positioning.one_line": 4}, history, windows=20)
        assert tags["positioning.one_line"] == "repeat"

    def test_appearing_in_every_recent_match_is_persistent(self) -> None:
        history = tuple(match(k, {"positioning.one_line": 2}) for k in ("a", "b", "c"))
        tags = tag_habits({"positioning.one_line": 2}, history, windows=20)
        assert tags["positioning.one_line"] == "persistent"

    def test_improving_beats_persistent(self) -> None:
        history = tuple(match(k, {"positioning.one_line": 8}) for k in ("a", "b", "c"))
        tags = tag_habits({"positioning.one_line": 1}, history, windows=20)
        assert tags["positioning.one_line"] == "improving"

    def test_only_recent_matches_count(self) -> None:
        old = tuple(match(k, {"positioning.one_line": 2}) for k in ("a", "b", "c"))
        recent = tuple(match(k, {"other.thing": 1}) for k in ("d", "e", "f"))
        tags = tag_habits({"positioning.one_line": 2}, old + recent, windows=20, lookback=3)
        assert tags["positioning.one_line"] == "new"

    def test_a_match_with_no_windows_is_ignored(self) -> None:
        history = (match("a", {"positioning.one_line": 4}, windows=0),)
        assert tag_habits({"positioning.one_line": 4}, history, windows=20) == {
            "positioning.one_line": "new"
        }

    def test_every_current_check_gets_a_tag(self) -> None:
        tags = tag_habits({"a.b": 1, "c.d": 2}, (), windows=10)
        assert set(tags) == {"a.b", "c.d"}


class TestPersistence:
    def test_appending_and_reading_back(self, tmp_path: Path) -> None:
        path = tmp_path / "habits.json"
        append_match(path, match("a", {"positioning.one_line": 2}))
        append_match(path, match("b", {"utility.has_purpose": 1}))
        history = read_history(path)
        assert [m.key for m in history] == ["a", "b"]
        assert history[0].counts == {"positioning.one_line": 2}

    def test_re_reviewing_a_clip_replaces_its_record(self, tmp_path: Path) -> None:
        path = tmp_path / "habits.json"
        append_match(path, match("a", {"positioning.one_line": 9}))
        append_match(path, match("a", {"positioning.one_line": 2}))
        history = read_history(path)
        assert len(history) == 1 and history[0].counts == {"positioning.one_line": 2}

    def test_a_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert read_history(tmp_path / "nope.json") == ()

    def test_a_corrupt_file_is_empty_not_a_crash(self, tmp_path: Path) -> None:
        # History is a nicety; it must never be able to fail a review.
        bad = tmp_path / "habits.json"
        bad.write_text("{not json", encoding="utf-8")
        assert read_history(bad) == ()
