import json
from pathlib import Path

import pytest

from round_review.errors import HudError
from round_review.vision.digits import (
    DigitTemplates,
    match_glyph,
    parse_clock,
    read_text,
    similarity,
)
from round_review.vision.raster import Glyph


def glyph(rows: list[str], aspect: float | None = None) -> Glyph:
    width = len(rows[0])
    return Glyph(tuple(rows), aspect if aspect is not None else width / len(rows))


ONE = glyph(
    [
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
    ]
)
SEVEN = glyph(
    [
        "######",
        "....#.",
        "...#..",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
        "..#...",
    ]
)
COLON = glyph(
    [
        "......",
        "..##..",
        "..##..",
        "......",
        "......",
        "......",
        "..##..",
        "..##..",
        "......",
        "......",
    ],
    aspect=0.25,
)


class TestSimilarity:
    def test_identical_grids_score_one(self) -> None:
        assert similarity(ONE, ONE) == pytest.approx(1.0)

    def test_different_grids_score_lower(self) -> None:
        assert similarity(ONE, SEVEN) < 0.95

    def test_aspect_mismatch_is_penalised(self) -> None:
        wide = Glyph(ONE.grid, aspect=2.0)
        narrow = Glyph(ONE.grid, aspect=0.25)
        assert similarity(wide, narrow) < 0.5
        assert similarity(wide, wide) == pytest.approx(1.0)


class TestMatchGlyph:
    def templates(self) -> DigitTemplates:
        return DigitTemplates({"1": (ONE,), "7": (SEVEN,), ":": (COLON,)})

    def test_picks_the_closest_character(self) -> None:
        assert match_glyph(ONE, self.templates(), 0.7) == ("1", pytest.approx(1.0))
        assert match_glyph(COLON, self.templates(), 0.7)[0] == ":"

    def test_returns_nothing_below_the_confidence_floor(self) -> None:
        noise = glyph(["######"] * 10)
        assert match_glyph(noise, self.templates(), 0.95) == (None, pytest.approx(0.0, abs=1))

    def test_an_empty_template_set_matches_nothing(self) -> None:
        assert match_glyph(ONE, DigitTemplates({}), 0.5)[0] is None

    def test_several_samples_per_character_take_the_best(self) -> None:
        smudged = glyph(
            [
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "###...",
            ]
        )
        templates = DigitTemplates({"1": (SEVEN, smudged)})
        char, score = match_glyph(smudged, templates, 0.7)
        assert char == "1"
        assert score == pytest.approx(1.0)


class TestReadText:
    def test_reads_glyphs_left_to_right(self) -> None:
        templates = DigitTemplates({"1": (ONE,), "7": (SEVEN,), ":": (COLON,)})
        text, confidence = read_text([ONE, COLON, SEVEN, ONE], templates, 0.7)
        assert text == "1:71"
        assert confidence == pytest.approx(1.0)

    def test_confidence_is_the_worst_glyph(self) -> None:
        templates = DigitTemplates({"1": (ONE,), ":": (COLON,)})
        smudged = glyph(
            [
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..#...",
                "..##..",
                "..#...",
            ]
        )
        _, confidence = read_text([ONE, smudged], templates, 0.5)
        assert confidence < 1.0

    def test_an_unmatched_glyph_gives_up(self) -> None:
        templates = DigitTemplates({"1": (ONE,)})
        text, confidence = read_text([ONE, COLON], templates, 0.9)
        assert text is None
        assert confidence == 0.0

    def test_no_glyphs_reads_nothing(self) -> None:
        assert read_text([], DigitTemplates({"1": (ONE,)}), 0.7) == (None, 0.0)


class TestParseClock:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [("1:39", 99.0), ("0:05", 5.0), ("12:00", 720.0), ("0:00", 0.0)],
    )
    def test_reads_a_clock(self, text: str, seconds: float) -> None:
        assert parse_clock(text) == seconds

    @pytest.mark.parametrize("text", ["139", "1:5", "1:60", "", ":39", "1:39:20", "a:bc"])
    def test_rejects_anything_that_is_not_a_clock(self, text: str) -> None:
        assert parse_clock(text) is None


class TestTemplateFile:
    def test_round_trips_through_json(self, tmp_path: Path) -> None:
        templates = DigitTemplates({"1": (ONE,), ":": (COLON,)})
        path = tmp_path / "digits.json"
        templates.save(path)
        loaded = DigitTemplates.load(path)
        assert loaded.characters() == {"1", ":"}
        assert match_glyph(ONE, loaded, 0.7)[0] == "1"

    def test_learning_adds_samples_without_losing_the_old_ones(self) -> None:
        templates = DigitTemplates({"1": (ONE,)})
        grown = templates.learn([(":", COLON), ("7", SEVEN)])
        assert grown.characters() == {"1", ":", "7"}
        assert templates.characters() == {"1"}  # the original is untouched

    def test_reports_which_digits_are_still_missing(self) -> None:
        assert DigitTemplates({"1": (ONE,), ":": (COLON,)}).missing() == [
            "0",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
        ]
        assert DigitTemplates({c: (ONE,) for c in "0123456789:"}).missing() == []

    def test_loading_a_missing_file_gives_an_empty_set(self, tmp_path: Path) -> None:
        assert DigitTemplates.load(tmp_path / "nope.json").characters() == set()

    def test_loading_junk_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "digits.json"
        path.write_text("not json")
        with pytest.raises(HudError, match="cannot read"):
            DigitTemplates.load(path)

    def test_rejects_a_malformed_template(self, tmp_path: Path) -> None:
        path = tmp_path / "digits.json"
        path.write_text(json.dumps({"characters": {"1": [{"grid": "notalist", "aspect": 1}]}}))
        with pytest.raises(HudError):
            DigitTemplates.load(path)
