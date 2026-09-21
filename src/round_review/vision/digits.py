"""Reading the round clock off a HUD crop by matching glyphs against learned templates.

Valorant's timer font is not a system font, so templates are learned from the player's own
footage: crop the timer, say what it reads, and the glyphs are stored. Matching is then
exact enough to be deterministic, and completely independent of the vision model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from round_review.errors import HudError
from round_review.vision.raster import Glyph

CLOCK_CHARACTERS: tuple[str, ...] = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ":")
CLOCK_RE = re.compile(r"^(\d{1,2}):([0-5]\d)$")


def _parse_glyph(sample: object) -> Glyph:
    """A stored glyph must be a rectangle of on/off cells; anything else is corrupt."""
    if not isinstance(sample, Mapping):
        raise ValueError("glyph sample is not an object")
    rows = sample.get("grid")
    if not isinstance(rows, list) or not rows:
        raise ValueError("glyph grid must be a non-empty list of rows")
    grid = tuple(str(row) for row in rows)
    if len({len(row) for row in grid}) != 1 or not grid[0]:
        raise ValueError("glyph grid rows must be non-empty and the same length")
    if any(set(row) - {"#", "."} for row in grid):
        raise ValueError("glyph grid may only contain '#' and '.'")
    return Glyph(grid, float(sample["aspect"]))


def similarity(a: Glyph, b: Glyph) -> float:
    """Cell agreement, scaled by how close the two glyph boxes are in shape.

    The aspect term keeps a narrow colon from matching a narrow digit like 1.
    """
    if len(a.grid) != len(b.grid) or len(a.grid[0]) != len(b.grid[0]):
        return 0.0
    cells_a, cells_b = a.cells(), b.cells()
    agree = sum(1 for x, y in zip(cells_a, cells_b, strict=True) if x == y) / len(cells_a)
    if a.aspect <= 0 or b.aspect <= 0:
        return agree
    shape = min(a.aspect, b.aspect) / max(a.aspect, b.aspect)
    return agree * shape


@dataclass(frozen=True, slots=True)
class DigitTemplates:
    """Learned glyph samples per character. Several samples per character are kept because
    the same digit renders slightly differently at different timer positions."""

    characters_to_samples: Mapping[str, tuple[Glyph, ...]]

    def characters(self) -> set[str]:
        return set(self.characters_to_samples)

    def missing(self) -> list[str]:
        """Clock characters with no sample yet, so the CLI can say what is left to teach."""
        return [c for c in CLOCK_CHARACTERS if c not in self.characters_to_samples]

    def learn(self, samples: Iterable[tuple[str, Glyph]]) -> DigitTemplates:
        grown = {k: list(v) for k, v in self.characters_to_samples.items()}
        for char, glyph in samples:
            grown.setdefault(char, []).append(glyph)
        return DigitTemplates({k: tuple(v) for k, v in grown.items()})

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "characters": {
                char: [{"grid": list(g.grid), "aspect": g.aspect} for g in samples]
                for char, samples in sorted(self.characters_to_samples.items())
            }
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> DigitTemplates:
        if not path.exists():
            return cls({})
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = payload["characters"]
            return cls(
                {
                    str(char): tuple(_parse_glyph(sample) for sample in samples)
                    for char, samples in raw.items()
                }
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise HudError(f"{path}: cannot read digit templates: {exc}") from exc


def match_glyph(
    glyph: Glyph, templates: DigitTemplates, min_confidence: float
) -> tuple[str | None, float]:
    best_char: str | None = None
    best_score = 0.0
    for char, samples in templates.characters_to_samples.items():
        for sample in samples:
            score = similarity(glyph, sample)
            if score > best_score:
                best_char, best_score = char, score
    if best_score < min_confidence:
        return None, 0.0
    return best_char, best_score


def read_text(
    glyphs: Sequence[Glyph], templates: DigitTemplates, min_confidence: float
) -> tuple[str | None, float]:
    """Read glyphs left to right. One unmatched glyph fails the whole read: a half-read
    clock is worse than no clock, because the phase rules depend on the number."""
    if not glyphs:
        return None, 0.0
    chars: list[str] = []
    worst = 1.0
    for glyph in glyphs:
        char, score = match_glyph(glyph, templates, min_confidence)
        if char is None:
            return None, 0.0
        chars.append(char)
        worst = min(worst, score)
    return "".join(chars), worst


def parse_clock(text: str | None) -> float | None:
    """Seconds from an 'M:SS' or 'MM:SS' reading, or None if it is not a clock."""
    if not text:
        return None
    match = CLOCK_RE.match(text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))
