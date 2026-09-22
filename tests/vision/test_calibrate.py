"""Picking the brightness cutoff by measurement instead of by guessing."""

from pathlib import Path

from round_review.vision.calibrate import ThresholdScore, best_threshold, score_thresholds
from round_review.vision.raster import Gray


def gray(width: int, height: int, values: list[int]) -> Gray:
    return Gray(width, height, bytes(values))


# Two glyph columns separated by a dark gap, on a mid-grey plate. A cutoff below the plate
# brightness swallows both into one blob; above it, they segment cleanly.
PLATE = 150
DIGIT = 240


def two_glyphs() -> Gray:
    rows = []
    for _ in range(6):
        rows += [DIGIT, DIGIT, PLATE, PLATE, DIGIT, DIGIT]
    return gray(6, 6, rows)


class TestScoring:
    def test_a_cutoff_above_the_plate_finds_both_glyphs(self) -> None:
        scores = score_thresholds([(two_glyphs(), 2)], (100, 200))
        by_value = {s.threshold: s for s in scores}
        assert by_value[200].exact == 1
        assert by_value[100].exact == 0

    def test_every_candidate_is_scored(self) -> None:
        scores = score_thresholds([(two_glyphs(), 2)], (100, 160, 200))
        assert [s.threshold for s in scores] == [100, 160, 200]
        assert all(s.total == 1 for s in scores)

    def test_the_miss_is_reported_with_what_it_saw(self) -> None:
        scores = score_thresholds([(two_glyphs(), 2)], (100,))
        assert scores[0].misses and scores[0].misses[0][1] != 2


class TestBest:
    def test_picks_the_middle_of_the_widest_run_that_works(self) -> None:
        scores = [
            ThresholdScore(100, 0, 1, ()),
            ThresholdScore(180, 1, 1, ()),
            ThresholdScore(190, 1, 1, ()),
            ThresholdScore(200, 1, 1, ()),
            ThresholdScore(250, 0, 1, ()),
        ]
        # Not the first that works: the middle, so drift in either direction is tolerated.
        assert best_threshold(scores) == 190

    def test_none_when_nothing_reads_correctly(self) -> None:
        assert best_threshold([ThresholdScore(100, 0, 2, ())]) is None

    def test_no_scores_means_none(self) -> None:
        assert best_threshold([]) is None

    def test_a_single_working_cutoff_is_returned(self) -> None:
        scores = [ThresholdScore(100, 0, 1, ()), ThresholdScore(200, 1, 1, ())]
        assert best_threshold(scores) == 200

    def test_the_widest_run_wins_over_an_earlier_narrow_one(self) -> None:
        scores = [
            ThresholdScore(120, 1, 1, ()),
            ThresholdScore(130, 0, 1, ()),
            ThresholdScore(200, 1, 1, ()),
            ThresholdScore(210, 1, 1, ()),
            ThresholdScore(220, 1, 1, ()),
        ]
        assert best_threshold(scores) == 210


# ------------------------------------------------------------ calibrating with no help

from round_review.vision.calibrate import auto_threshold, clock_like_score  # noqa: E402
from round_review.vision.digits import DigitTemplates  # noqa: E402


class TestClockLikeScore:
    """The clock is its own answer key: a cutoff that produces readable M:SS strings is
    right, and one that produces noise is wrong. No user input needed."""

    def test_counts_only_readings_that_parse_as_a_clock(self) -> None:
        assert clock_like_score(["1:39", "1:38", None, "garbage", "0:05"]) == 3

    def test_nothing_readable_scores_zero(self) -> None:
        assert clock_like_score([None, None]) == 0

    def test_an_impossible_clock_does_not_count(self) -> None:
        # 1:75 is not a time; a cutoff producing it is mis-segmenting.
        assert clock_like_score(["1:75", "2:99"]) == 0


class TestAutoThreshold:
    def test_picks_the_cutoff_that_reads_the_most_clocks(self) -> None:
        def reader(threshold: int) -> list[str | None]:
            return ["1:39", "1:38", "0:17"] if threshold >= 190 else [None, "junk", None]

        assert auto_threshold(reader, candidates=(150, 190, 200, 210)) == 200

    def test_none_when_no_cutoff_reads_anything(self) -> None:
        assert auto_threshold(lambda _t: [None, None], candidates=(150, 200)) is None

    def test_prefers_the_middle_of_the_best_run(self) -> None:
        def reader(threshold: int) -> list[str | None]:
            return ["1:39"] if threshold in (180, 190, 200) else [None]

        assert auto_threshold(reader, candidates=(170, 180, 190, 200, 210)) == 190


class TestBundledTemplates:
    def test_the_package_ships_every_clock_character(self) -> None:
        from round_review.vision.digits import bundled_templates

        assert bundled_templates().missing() == []

    def test_player_samples_are_added_to_the_bundled_ones(self, tmp_path: Path) -> None:
        from round_review.vision.digits import bundled_templates, load_templates
        from round_review.vision.raster import Glyph

        extra = DigitTemplates({"7": (Glyph(("#" * 6,) * 10, 0.5),)})
        path = extra.save(tmp_path / "mine.json")
        merged = load_templates(path)
        assert merged.missing() == []
        assert len(merged.characters_to_samples["7"]) > len(
            bundled_templates().characters_to_samples["7"]
        )

    def test_no_player_file_still_reads_the_clock(self, tmp_path: Path) -> None:
        from round_review.vision.digits import load_templates

        assert load_templates(tmp_path / "absent.json").missing() == []
