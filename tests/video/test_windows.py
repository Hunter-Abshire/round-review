from itertools import pairwise

import pytest

from round_review.video.windows import Window, select_windows


def test_short_video_yields_one_window_from_start() -> None:
    windows = select_windows(duration_s=5.0, window_s=12.0, count=3, edge_skip_s=30.0)
    assert windows == [Window(index=0, start_s=0.0, end_s=5.0, source="evenly_spaced")]


def test_video_shorter_than_edges_plus_window_uses_whole_middle() -> None:
    windows = select_windows(duration_s=60.0, window_s=12.0, count=3, edge_skip_s=30.0)
    assert len(windows) == 1
    assert windows[0].start_s == pytest.approx(24.0)
    assert windows[0].end_s == pytest.approx(36.0)


def test_evenly_spaced_within_usable_range() -> None:
    windows = select_windows(duration_s=600.0, window_s=12.0, count=3, edge_skip_s=30.0)
    assert [w.index for w in windows] == [0, 1, 2]
    assert all(w.end_s - w.start_s == pytest.approx(12.0) for w in windows)
    assert windows[0].start_s >= 30.0
    assert windows[-1].end_s <= 570.0
    gaps = [b.start_s - a.start_s for a, b in pairwise(windows)]
    assert gaps[0] == pytest.approx(gaps[1])


def test_windows_never_overlap() -> None:
    windows = select_windows(duration_s=100.0, window_s=12.0, count=3, edge_skip_s=30.0)
    for a, b in pairwise(windows):
        assert a.end_s <= b.start_s


def test_count_reduced_when_range_too_small_for_all() -> None:
    windows = select_windows(duration_s=90.0, window_s=12.0, count=5, edge_skip_s=30.0)
    assert 1 <= len(windows) <= 2


def test_deterministic() -> None:
    a = select_windows(duration_s=333.3, window_s=12.0, count=3, edge_skip_s=30.0)
    b = select_windows(duration_s=333.3, window_s=12.0, count=3, edge_skip_s=30.0)
    assert a == b


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_invalid_duration_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        select_windows(duration_s=bad, window_s=12.0, count=3, edge_skip_s=30.0)


# --------------------------------------------------------------------- round coverage


from round_review.video.windows import round_windows  # noqa: E402
from round_review.vision.timeline import RoundSpan  # noqa: E402


def spans(*bounds: tuple[float, float]) -> tuple[RoundSpan, ...]:
    return tuple(RoundSpan(i, a, b) for i, (a, b) in enumerate(bounds, start=1))


class TestRoundWindows:
    def test_each_round_gets_its_opening_and_its_decisive_end(self) -> None:
        got = round_windows(spans((100.0, 160.0)), window_s=12.0, duration_s=600.0)
        assert [(w.start_s, w.end_s, w.source) for w in got] == [
            (100.0, 112.0, "round_start"),
            (148.0, 160.0, "round_end"),
        ]

    def test_a_short_round_gets_one_window(self) -> None:
        got = round_windows(spans((100.0, 115.0)), window_s=12.0, duration_s=600.0)
        assert len(got) == 1
        assert got[0].start_s == 100.0

    def test_windows_are_numbered_across_rounds(self) -> None:
        got = round_windows(spans((0.0, 60.0), (60.0, 120.0)), window_s=12.0, duration_s=600.0)
        assert [w.index for w in got] == [0, 1, 2, 3]

    def test_death_anchors_add_windows_centred_on_the_death(self) -> None:
        got = round_windows(spans((0.0, 120.0)), window_s=12.0, duration_s=600.0, deaths=(60.0,))
        centred = [w for w in got if w.source == "death"]
        assert [(w.start_s, w.end_s) for w in centred] == [(51.0, 63.0)]

    def test_a_death_window_never_leaves_the_recording(self) -> None:
        got = round_windows(spans((0.0, 10.0)), window_s=12.0, duration_s=600.0, deaths=(2.0,))
        death = [w for w in got if w.source == "death"]
        assert death and death[0].start_s == 0.0

    def test_overlapping_windows_are_merged(self) -> None:
        # A death right at the barrier drop must not review the same seconds twice.
        got = round_windows(spans((100.0, 160.0)), window_s=12.0, duration_s=600.0, deaths=(106.0,))
        starts = sorted(w.start_s for w in got)
        assert all(b - a >= 12.0 for a, b in pairwise(starts))

    def test_the_budget_keeps_the_rounds_spread_out(self) -> None:
        many = spans(*[(float(i * 60), float(i * 60 + 60)) for i in range(10)])
        got = round_windows(many, window_s=12.0, duration_s=600.0, max_windows=5)
        assert len(got) == 5
        # not just the first five: the last round is still represented
        assert max(w.start_s for w in got) > 400.0

    def test_no_rounds_means_no_windows(self) -> None:
        assert round_windows((), window_s=12.0, duration_s=600.0) == []


def test_the_round_ending_window_stops_at_the_end_of_live_play() -> None:
    # span runs to 200s but live play stopped at 160s; the rest is the next buy phase
    span = RoundSpan(1, 100.0, 200.0, live_end_s=160.0)
    got = round_windows((span,), window_s=12.0, duration_s=600.0)
    ends = [(w.start_s, w.end_s) for w in got if w.source == "round_end"]
    assert ends == [(148.0, 160.0)]


def test_a_span_with_no_live_end_recorded_falls_back_to_its_end() -> None:
    span = RoundSpan(1, 100.0, 160.0, live_end_s=None)
    got = round_windows((span,), window_s=12.0, duration_s=600.0)
    ends = [(w.start_s, w.end_s) for w in got if w.source == "round_end"]
    assert ends == [(148.0, 160.0)]


def test_a_round_that_is_all_buy_phase_gets_no_windows() -> None:
    # A recording that starts mid-shop has a first "round" with no live play in it at all.
    span = RoundSpan(1, 0.0, 24.0, live_end_s=0.0)
    assert round_windows((span,), window_s=12.0, duration_s=600.0) == []
