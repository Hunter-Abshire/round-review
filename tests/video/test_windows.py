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
