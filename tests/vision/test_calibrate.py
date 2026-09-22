"""Picking the brightness cutoff by measurement instead of by guessing."""

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
