"""Grayscale raster primitives, in pure Python.

ffmpeg does the decoding, cropping and scaling and hands us a tiny PGM, so reading the HUD
needs no imaging dependency. The rasters involved are a hundred pixels wide, so plain
Python is fast enough and keeps the packaged app small.
"""

from __future__ import annotations

from dataclasses import dataclass

from round_review.errors import HudError

# A glyph is normalised onto this grid before matching, so size and font hinting differences
# between frames do not matter.
GRID_COLS = 6
GRID_ROWS = 10


@dataclass(frozen=True, slots=True)
class Gray:
    width: int
    height: int
    pixels: bytes

    def at(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]


@dataclass(frozen=True, slots=True)
class GlyphBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Glyph:
    """A normalised glyph: a small on/off grid plus the aspect ratio it came from."""

    grid: tuple[str, ...]
    aspect: float

    def cells(self) -> str:
        return "".join(self.grid)


def _tokens(raw: bytes) -> tuple[list[bytes], int]:
    """The first three header tokens after the magic, and the offset the pixels start at."""
    tokens: list[bytes] = []
    i = 2
    while len(tokens) < 3:
        while i < len(raw) and raw[i : i + 1].isspace():
            i += 1
        if i < len(raw) and raw[i : i + 1] == b"#":
            while i < len(raw) and raw[i : i + 1] != b"\n":
                i += 1
            continue
        start = i
        while i < len(raw) and not raw[i : i + 1].isspace():
            i += 1
        if start == i:
            raise HudError("PGM header ended early")
        tokens.append(raw[start:i])
    return tokens, i + 1


def parse_pgm(raw: bytes) -> Gray:
    """Parse a binary (P5) 8-bit PGM, the format ffmpeg writes for a gray crop."""
    if not raw.startswith(b"P5"):
        raise HudError("not a binary PGM (expected a P5 header from ffmpeg)")
    tokens, offset = _tokens(raw)
    try:
        width, height, maxval = (int(t) for t in tokens)
    except ValueError as exc:
        raise HudError(f"PGM header is malformed: {exc}") from exc
    if width <= 0 or height <= 0:
        raise HudError(f"PGM has empty dimensions {width}x{height}")
    if maxval > 255:
        raise HudError("16-bit PGM is not supported; ask ffmpeg for gray, not gray16")
    pixels = raw[offset : offset + width * height]
    if len(pixels) < width * height:
        raise HudError(f"PGM is truncated: expected {width * height} bytes, got {len(pixels)}")
    return Gray(width, height, pixels)


def crop(gray: Gray, x: int, y: int, width: int, height: int) -> Gray:
    x, y = max(0, x), max(0, y)
    width = min(width, gray.width - x)
    height = min(height, gray.height - y)
    if width <= 0 or height <= 0:
        raise HudError("crop rectangle falls outside the raster")
    rows = [
        gray.pixels[(y + row) * gray.width + x : (y + row) * gray.width + x + width]
        for row in range(height)
    ]
    return Gray(width, height, b"".join(rows))


def _threshold_for(gray: Gray) -> int:
    """Midway between the darkest and brightest pixels, floored so a flat raster lights
    nothing. HUD text is bright on a dark background, so a simple split is enough."""
    low, high = min(gray.pixels), max(gray.pixels)
    if high - low < 30:
        return 256  # nothing meaningful in this crop
    return low + (high - low) // 2


def binarize(gray: Gray, threshold: int | None) -> tuple[bool, ...]:
    cutoff = _threshold_for(gray) if threshold is None else threshold
    return tuple(value >= cutoff for value in gray.pixels)


def _lit_columns(gray: Gray, threshold: int) -> list[int]:
    lit = binarize(gray, threshold if threshold >= 0 else None)
    return [
        x for x in range(gray.width) if any(lit[y * gray.width + x] for y in range(gray.height))
    ]


def segment_glyphs(
    gray: Gray, threshold: int, min_gap: int = 1, min_pixels: int = 1
) -> list[GlyphBox]:
    """Split a raster into glyph boxes on blank columns, trimming each box to its content."""
    columns = _lit_columns(gray, threshold)
    if not columns:
        return []
    groups: list[list[int]] = [[columns[0]]]
    for column in columns[1:]:
        if column - groups[-1][-1] > min_gap:
            groups.append([column])
        else:
            groups[-1].append(column)

    lit = binarize(gray, threshold if threshold >= 0 else None)
    boxes: list[GlyphBox] = []
    for group in groups:
        x0, x1 = group[0], group[-1]
        rows = [
            y for y in range(gray.height) if any(lit[y * gray.width + x] for x in range(x0, x1 + 1))
        ]
        count = sum(1 for y in rows for x in range(x0, x1 + 1) if lit[y * gray.width + x])
        if count < min_pixels:
            continue
        boxes.append(GlyphBox(x0, rows[0], x1 - x0 + 1, rows[-1] - rows[0] + 1))
    return boxes


def normalize_glyph(
    gray: Gray,
    x: int,
    y: int,
    width: int,
    height: int,
    threshold: int,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> Glyph:
    """Sample a glyph box onto a fixed grid so glyphs of different sizes compare directly."""
    lit = binarize(gray, threshold if threshold >= 0 else None)
    grid: list[str] = []
    for row in range(rows):
        source_y = y + min(height - 1, (row * height) // rows)
        line = []
        for col in range(cols):
            source_x = x + min(width - 1, (col * width) // cols)
            line.append("#" if lit[source_y * gray.width + source_x] else ".")
        grid.append("".join(line))
    return Glyph(tuple(grid), width / height if height else 0.0)
