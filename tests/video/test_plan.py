from itertools import pairwise

import pytest

from round_review.video.windows import Window, plan_windows, tile_windows


def spans(windows: list[Window]) -> list[tuple[float, float]]:
    return [(round(w.start_s, 3), round(w.end_s, 3)) for w in windows]


def covered(windows: list[Window]) -> float:
    return sum(w.end_s - w.start_s for w in windows)


class TestTileWindows:
    def test_tiles_contiguously_across_the_usable_span(self) -> None:
        windows = tile_windows(duration_s=100.0, window_s=12.0, edge_skip_s=10.0)
        assert spans(windows)[0] == (10.0, 22.0)
        assert spans(windows)[-1][1] == pytest.approx(90.0)
        for a, b in pairwise(windows):
            assert a.end_s == pytest.approx(b.start_s)
        assert all(w.source == "tiled" for w in windows)
        assert [w.index for w in windows] == list(range(len(windows)))

    def test_keeps_a_trailing_partial_window_when_at_least_half_a_window(self) -> None:
        # usable 10..88 = 78s -> six full 12s windows plus a 6s tail
        windows = tile_windows(duration_s=98.0, window_s=12.0, edge_skip_s=10.0)
        assert spans(windows)[-1] == (82.0, 88.0)

    def test_drops_a_trailing_sliver(self) -> None:
        # usable 10..85 = 75s -> six full windows plus a 3s tail, dropped
        windows = tile_windows(duration_s=95.0, window_s=12.0, edge_skip_s=10.0)
        assert spans(windows)[-1] == (70.0, 82.0)

    def test_short_recording_becomes_one_window(self) -> None:
        assert spans(tile_windows(duration_s=8.0, window_s=12.0, edge_skip_s=30.0)) == [(0.0, 8.0)]

    def test_edge_skip_is_ignored_when_it_would_leave_nothing(self) -> None:
        windows = tile_windows(duration_s=40.0, window_s=12.0, edge_skip_s=30.0)
        assert covered(windows) > 0
        assert windows[0].start_s >= 0.0
        assert windows[-1].end_s <= 40.0

    def test_max_span_limits_review_to_the_first_n_seconds_of_gameplay(self) -> None:
        windows = tile_windows(duration_s=749.0, window_s=12.0, edge_skip_s=30.0, max_span_s=60.0)
        assert spans(windows) == [
            (30.0, 42.0),
            (42.0, 54.0),
            (54.0, 66.0),
            (66.0, 78.0),
            (78.0, 90.0),
        ]

    def test_max_windows_spreads_evenly_instead_of_truncating(self) -> None:
        windows = tile_windows(duration_s=749.0, window_s=12.0, edge_skip_s=30.0, max_windows=4)
        assert len(windows) == 4
        assert windows[0].start_s == pytest.approx(30.0)
        assert windows[-1].end_s == pytest.approx(719.0)
        gaps = [b.start_s - a.start_s for a, b in pairwise(windows)]
        assert gaps[0] == pytest.approx(gaps[1]) == pytest.approx(gaps[2])
        assert all(w.source == "evenly_spaced" for w in windows)

    def test_max_windows_above_the_tile_count_is_a_no_op(self) -> None:
        full = tile_windows(duration_s=120.0, window_s=12.0, edge_skip_s=10.0)
        assert (
            tile_windows(duration_s=120.0, window_s=12.0, edge_skip_s=10.0, max_windows=99) == full
        )

    def test_full_coverage_of_a_long_recording_is_near_total(self) -> None:
        windows = tile_windows(duration_s=749.0, window_s=12.0, edge_skip_s=30.0)
        assert covered(windows) / 749.0 > 0.85
        assert len(windows) == 57

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_duration_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError):
            tile_windows(duration_s=bad, window_s=12.0, edge_skip_s=0.0)


class TestPlanWindows:
    def test_full_coverage_tiles(self) -> None:
        windows = plan_windows(
            300.0, window_s=12.0, coverage="full", windows_per_file=3, edge_skip_s=30.0
        )
        assert len(windows) == 20
        assert all(w.source == "tiled" for w in windows)

    def test_sampled_coverage_uses_windows_per_file(self) -> None:
        windows = plan_windows(
            300.0, window_s=12.0, coverage="sampled", windows_per_file=3, edge_skip_s=30.0
        )
        assert len(windows) == 3
        assert all(w.source == "evenly_spaced" for w in windows)

    def test_full_coverage_respects_max_windows_and_max_span(self) -> None:
        windows = plan_windows(
            749.0,
            window_s=12.0,
            coverage="full",
            windows_per_file=3,
            edge_skip_s=30.0,
            max_windows=10,
        )
        assert len(windows) == 10
        first_minute = plan_windows(
            749.0,
            window_s=12.0,
            coverage="full",
            windows_per_file=3,
            edge_skip_s=30.0,
            max_span_s=60.0,
        )
        assert len(first_minute) == 5

    def test_unknown_coverage_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="coverage"):
            plan_windows(
                300.0,
                window_s=12.0,
                coverage="whatever",  # type: ignore[arg-type]
                windows_per_file=3,
                edge_skip_s=30.0,
            )


def test_estimate_windows_matches_the_plan() -> None:
    from round_review.video.windows import estimate_window_count

    for duration in (45.0, 120.0, 749.0):
        planned = plan_windows(
            duration, window_s=12.0, coverage="full", windows_per_file=3, edge_skip_s=30.0
        )
        assert estimate_window_count(
            duration, window_s=12.0, coverage="full", windows_per_file=3, edge_skip_s=30.0
        ) == len(planned)
