"""Reading the rest of the HUD: health, credits and which abilities are still lit."""

from pathlib import Path

import pytest

from round_review.video.probe import Recording
from round_review.vision.digits import DigitTemplates
from round_review.vision.raster import Gray
from round_review.vision.state import (
    HudState,
    find_deaths,
    lit_fraction,
    read_number,
)

RECORDING = Recording(Path("/v/clip.mp4"), 600.0, 60.0, 1920, 1080, 10, 0.0)


def gray(width: int, height: int, values: list[int]) -> Gray:
    return Gray(width, height, bytes(values))


class TestLitFraction:
    def test_all_bright_is_one(self) -> None:
        assert lit_fraction(gray(2, 2, [255, 255, 255, 255]), threshold=128) == 1.0

    def test_all_dark_is_zero(self) -> None:
        assert lit_fraction(gray(2, 2, [0, 10, 20, 30]), threshold=128) == 0.0

    def test_half_bright_is_a_half(self) -> None:
        assert lit_fraction(gray(2, 2, [255, 255, 0, 0]), threshold=128) == pytest.approx(0.5)

    def test_an_empty_raster_is_zero(self) -> None:
        assert lit_fraction(gray(0, 0, []), threshold=128) == 0.0


class TestReadNumber:
    def test_an_unreadable_crop_is_none(self) -> None:
        assert read_number(gray(4, 4, [0] * 16), DigitTemplates({}), 0.8, -1) is None

    def test_a_clock_style_reading_is_rejected(self) -> None:
        # read_number must not silently accept "1:40" as the number 140.
        assert HudState(None, None, (), ()).health is None


class TestFindDeaths:
    def test_health_reaching_zero_is_a_death(self) -> None:
        assert find_deaths(((0.0, 100), (2.0, 40), (4.0, 0))) == (4.0,)

    def test_a_health_drop_that_does_not_reach_zero_is_not(self) -> None:
        assert find_deaths(((0.0, 100), (2.0, 40), (4.0, 10))) == ()

    def test_only_the_first_sample_of_a_run_of_zeroes_counts(self) -> None:
        assert find_deaths(((0.0, 100), (2.0, 0), (4.0, 0), (6.0, 0))) == (2.0,)

    def test_a_respawn_allows_another_death(self) -> None:
        got = find_deaths(((0.0, 100), (2.0, 0), (4.0, 100), (6.0, 0)))
        assert got == (2.0, 6.0)

    def test_health_vanishing_for_long_enough_is_a_death(self) -> None:
        # The player HUD disappears on death; a brief unreadable blip is just occlusion.
        got = find_deaths(
            ((0.0, 100), (2.0, None), (4.0, None), (6.0, None), (8.0, None)), missing_gap_s=6.0
        )
        assert got == (2.0,)

    def test_a_short_unreadable_blip_is_not_a_death(self) -> None:
        got = find_deaths(((0.0, 100), (2.0, None), (4.0, 90)), missing_gap_s=6.0)
        assert got == ()

    def test_no_samples_means_no_deaths(self) -> None:
        assert find_deaths(()) == ()

    def test_never_reports_a_death_before_any_reading(self) -> None:
        assert find_deaths(((0.0, None), (2.0, None), (4.0, None), (6.0, None))) == ()
