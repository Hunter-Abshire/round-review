"""Estimating how long a review will take, from what past reviews actually took."""

from datetime import UTC, datetime

import pytest

from round_review.ledger import LedgerEntry
from round_review.pacing import estimate_seconds, format_duration, measured_seconds_per_window


def entry(windows: int, duration_s: float, status: str = "ok") -> LedgerEntry:
    return LedgerEntry(
        key=f"k{windows}{duration_s}",
        path="/v/a.mp4",
        processed_at=datetime(2026, 9, 21, tzinfo=UTC),
        model_calls=windows * 2,
        report_path=None,
        status=status,  # type: ignore[arg-type]
        error=None,
        windows=windows,
        duration_s=duration_s,
    )


class TestMeasuredRate:
    def test_averages_the_recent_reviews(self) -> None:
        entries = [entry(10, 100.0), entry(10, 200.0)]
        assert measured_seconds_per_window(entries) == pytest.approx(15.0)

    def test_weights_by_window_count_not_by_review(self) -> None:
        # a 1-window review should not count as much as a 50-window one
        entries = [entry(1, 60.0), entry(50, 500.0)]
        assert measured_seconds_per_window(entries) == pytest.approx(560.0 / 51)

    def test_ignores_reviews_with_no_timing(self) -> None:
        stale = LedgerEntry("k", "/v/a.mp4", datetime(2026, 9, 20, tzinfo=UTC), 2, None, "ok", None)
        assert measured_seconds_per_window([stale, entry(10, 100.0)]) == pytest.approx(10.0)

    def test_ignores_failures_which_stop_early(self) -> None:
        assert measured_seconds_per_window([entry(2, 4.0, status="failed"), entry(10, 100.0)]) == (
            pytest.approx(10.0)
        )

    def test_only_the_most_recent_reviews_count(self) -> None:
        old = [entry(10, 1000.0) for _ in range(10)]
        recent = [entry(10, 100.0) for _ in range(5)]
        assert measured_seconds_per_window(old + recent, sample=5) == pytest.approx(10.0)

    def test_nothing_measured_yet(self) -> None:
        assert measured_seconds_per_window([]) is None


class TestEstimate:
    def test_multiplies_the_rate_by_the_windows(self) -> None:
        assert estimate_seconds(57, [entry(10, 100.0)]) == pytest.approx(570.0)

    def test_no_history_means_no_estimate(self) -> None:
        assert estimate_seconds(57, []) is None

    def test_no_windows_means_no_estimate(self) -> None:
        assert estimate_seconds(0, [entry(10, 100.0)]) is None


class TestFormat:
    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (45.0, "under a minute"),
            (90.0, "about 2 minutes"),
            (1800.0, "about 30 minutes"),
            (3600.0, "about 1 hour"),
            (7200.0, "about 2 hours"),
            (8100.0, "about 2 hours 15 minutes"),
        ],
    )
    def test_reads_like_a_human_estimate(self, seconds: float, text: str) -> None:
        assert format_duration(seconds) == text
