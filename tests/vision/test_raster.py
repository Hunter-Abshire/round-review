import pytest

from round_review.errors import HudError
from round_review.vision.raster import (
    Gray,
    binarize,
    crop,
    normalize_glyph,
    parse_pgm,
    segment_glyphs,
)


def make(width: int, height: int, rows: list[str]) -> Gray:
    """Build a raster from '#' (bright) and '.' (dark) rows."""
    pixels = bytes(255 if ch == "#" else 0 for row in rows for ch in row)
    return Gray(width, height, pixels)


class TestParsePgm:
    def header(self, width: int, height: int) -> bytes:
        return f"P5\n{width} {height}\n255\n".encode()

    def test_reads_a_binary_pgm(self) -> None:
        gray = parse_pgm(self.header(3, 2) + bytes([0, 128, 255, 10, 20, 30]))
        assert (gray.width, gray.height) == (3, 2)
        assert gray.at(1, 0) == 128
        assert gray.at(2, 1) == 30

    def test_tolerates_comments_and_extra_whitespace(self) -> None:
        raw = b"P5\n# made by ffmpeg\n2  2\n255\n" + bytes([1, 2, 3, 4])
        assert parse_pgm(raw).at(1, 1) == 4

    def test_rejects_a_non_pgm(self) -> None:
        with pytest.raises(HudError, match="not a binary PGM"):
            parse_pgm(b"\x89PNG\r\n")

    def test_rejects_a_truncated_file(self) -> None:
        with pytest.raises(HudError, match="truncated"):
            parse_pgm(self.header(4, 4) + bytes([0, 0]))

    def test_rejects_nonsense_dimensions(self) -> None:
        with pytest.raises(HudError):
            parse_pgm(b"P5\n0 0\n255\n")


class TestCrop:
    def test_takes_a_sub_rectangle(self) -> None:
        gray = make(4, 3, ["#..#", ".##.", "#..#"])
        inner = crop(gray, 1, 1, 2, 1)
        assert (inner.width, inner.height) == (2, 1)
        assert inner.pixels == bytes([255, 255])

    def test_clamps_to_the_raster(self) -> None:
        gray = make(2, 2, ["#.", ".#"])
        assert crop(gray, 1, 1, 10, 10).width == 1

    def test_rejects_an_empty_rectangle(self) -> None:
        with pytest.raises(HudError):
            crop(make(2, 2, ["##", "##"]), 5, 5, 1, 1)


class TestBinarize:
    def test_splits_on_the_threshold(self) -> None:
        gray = Gray(3, 1, bytes([10, 200, 255]))
        assert binarize(gray, 128) == (False, True, True)

    def test_otsu_like_default_handles_dim_text(self) -> None:
        # dim text on a dark background still separates
        gray = Gray(4, 1, bytes([5, 8, 90, 95]))
        assert binarize(gray, None) == (False, False, True, True)

    def test_a_uniform_raster_has_nothing_lit(self) -> None:
        assert binarize(Gray(3, 1, bytes([40, 40, 40])), None) == (False, False, False)


class TestSegmentGlyphs:
    def test_splits_on_blank_columns(self) -> None:
        gray = make(7, 3, ["#.#.###", "#.#...#", "#.#.###"])
        boxes = segment_glyphs(gray, threshold=128, min_gap=1)
        assert [(b.x, b.width) for b in boxes] == [(0, 1), (2, 1), (4, 3)]

    def test_trims_rows_to_the_glyph(self) -> None:
        gray = make(3, 4, ["...", ".#.", ".#.", "..."])
        (box,) = segment_glyphs(gray, threshold=128, min_gap=1)
        assert (box.x, box.y, box.width, box.height) == (1, 1, 1, 2)

    def test_ignores_single_column_gaps_inside_a_glyph_when_asked(self) -> None:
        gray = make(5, 1, ["##.##"])
        assert len(segment_glyphs(gray, threshold=128, min_gap=2)) == 1
        assert len(segment_glyphs(gray, threshold=128, min_gap=1)) == 2

    def test_drops_specks_below_the_minimum_size(self) -> None:
        gray = make(5, 3, ["#..##", "...##", "...##"])
        boxes = segment_glyphs(gray, threshold=128, min_gap=1, min_pixels=2)
        assert [(b.x, b.width) for b in boxes] == [(3, 2)]

    def test_an_empty_raster_has_no_glyphs(self) -> None:
        assert segment_glyphs(make(3, 2, ["...", "..."]), threshold=128, min_gap=1) == []


class TestNormalizeGlyph:
    def test_samples_a_box_onto_a_fixed_grid(self) -> None:
        gray = make(4, 4, ["####", "#..#", "#..#", "####"])
        glyph = normalize_glyph(gray, x=0, y=0, width=4, height=4, threshold=128, cols=4, rows=4)
        assert glyph.grid == ("####", "#..#", "#..#", "####")
        assert glyph.aspect == pytest.approx(1.0)

    def test_scales_a_larger_box_down(self) -> None:
        gray = make(8, 8, ["########"] * 8)
        glyph = normalize_glyph(gray, 0, 0, 8, 8, threshold=128, cols=4, rows=4)
        assert glyph.grid == ("####",) * 4

    def test_keeps_the_aspect_ratio_of_the_source_box(self) -> None:
        gray = make(2, 8, ["##"] * 8)
        glyph = normalize_glyph(gray, 0, 0, 2, 8, threshold=128, cols=4, rows=4)
        assert glyph.aspect == pytest.approx(0.25)
