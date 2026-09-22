"""The situation pass decides whether to coach, so it runs for every window. Sending it a
dozen images makes that decision the dominant cost of a review."""

from pathlib import Path

import pytest

from round_review.coaching.frames import select_situation_frames
from round_review.video.frames import FrameSample


def samples(count: int) -> list[FrameSample]:
    return [FrameSample(0, float(i), Path(f"/f/{i:03d}.jpg")) for i in range(count)]


def times(chosen: list[FrameSample]) -> list[float]:
    return [s.timestamp_s for s in chosen]


def test_a_single_frame_is_the_middle_of_the_window() -> None:
    assert times(select_situation_frames(samples(12), 1)) == [6.0]


def test_three_frames_span_the_window() -> None:
    assert times(select_situation_frames(samples(12), 3)) == [0.0, 6.0, 11.0]


def test_frames_stay_in_order_and_are_never_repeated() -> None:
    chosen = times(select_situation_frames(samples(12), 5))
    assert chosen == sorted(chosen)
    assert len(set(chosen)) == 5


def test_asking_for_more_than_there_are_returns_them_all() -> None:
    assert times(select_situation_frames(samples(3), 10)) == [0.0, 1.0, 2.0]


def test_zero_or_fewer_means_every_frame() -> None:
    assert len(select_situation_frames(samples(12), 0)) == 12
    assert len(select_situation_frames(samples(12), -1)) == 12


def test_an_empty_window_stays_empty() -> None:
    assert select_situation_frames([], 3) == []


@pytest.mark.parametrize("count", [1, 2, 3, 4, 7, 12])
def test_always_returns_exactly_what_was_asked_for(count: int) -> None:
    assert len(select_situation_frames(samples(12), count)) == count
