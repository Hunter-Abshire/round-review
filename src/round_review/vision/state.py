"""The rest of the HUD, read deterministically: health, credits and lit ability icons.

The most valuable things a coach says are about resources. "You died with all four
abilities up" and "you force-bought on 2400 and lost the round" are the lines that change
how someone plays, and both are currently guesses a vision model makes from a thumbnail.
Valorant draws all of it at fixed screen positions, so none of it needs to be guessed.

Everything here is optional and silent when unconfigured, for the same reason the clock
reader is: a HUD region that has never been checked against real footage must degrade to
"unknown", never to a confident wrong answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from round_review.errors import RoundReviewError
from round_review.video.probe import CommandRunner, Recording
from round_review.vision.digits import DigitTemplates, read_text
from round_review.vision.hud import CROP_SCALE, Region, build_crop_args
from round_review.vision.raster import Gray, normalize_glyph, parse_pgm, segment_glyphs

NUMBER_RE = re.compile(r"^\d{1,5}$")
# An icon this much lit counts as available. Calibrate with `hud state` on real footage.
DEFAULT_LIT_FRACTION = 0.25
# How long the player HUD must stay unreadable before it means death rather than occlusion.
DEFAULT_MISSING_GAP_S = 6.0


@dataclass(frozen=True, slots=True)
class HudState:
    """What the HUD said at one moment. Every field is None or empty when unconfigured."""

    health: int | None
    credits: int | None
    abilities_lit: tuple[bool, ...]
    errors: tuple[str, ...]

    def describe(self) -> str:
        parts: list[str] = []
        if self.health is not None:
            parts.append(f"health={self.health}")
        if self.credits is not None:
            parts.append(f"credits={self.credits}")
        if self.abilities_lit:
            up = sum(self.abilities_lit)
            parts.append(f"abilities up={up} of {len(self.abilities_lit)}")
        return "; ".join(parts)


def lit_fraction(gray: Gray, threshold: int) -> float:
    """How much of a crop is brighter than the cutoff. A used ability icon is dimmed."""
    total = gray.width * gray.height
    if total <= 0:
        return 0.0
    return sum(1 for value in gray.pixels if value > threshold) / total


def read_number(
    gray: Gray, templates: DigitTemplates, min_confidence: float, threshold: int
) -> int | None:
    """Read a plain integer off a crop. Anything with punctuation in it is not a number:
    the clock reads "1:40" through the same templates and must never become 140."""
    boxes = segment_glyphs(gray, threshold=threshold, min_gap=1, min_pixels=3)
    if not boxes:
        return None
    glyphs = [
        normalize_glyph(gray, b.x, b.y, b.width, b.height, threshold=threshold) for b in boxes
    ]
    text, _confidence = read_text(glyphs, templates, min_confidence)
    if text is None or not NUMBER_RE.match(text):
        return None
    return int(text)


def _crop(
    recording: Recording,
    timestamp_s: float,
    region: Region,
    runner: CommandRunner,
    out_path: Path,
    threshold_scale: int = CROP_SCALE,
) -> Gray | None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        runner.run(
            build_crop_args(
                recording.path, timestamp_s, region, recording, out_path, threshold_scale
            )
        )
        return parse_pgm(out_path.read_bytes())
    except (RoundReviewError, OSError):
        return None


def read_state(
    recording: Recording,
    timestamp_s: float,
    regions: Mapping[str, Region],
    ability_regions: Sequence[Region],
    runner: CommandRunner,
    templates: DigitTemplates,
    out_dir: Path,
    min_confidence: float,
    threshold: int = -1,
    lit_threshold: int = 128,
    lit_min_fraction: float = DEFAULT_LIT_FRACTION,
) -> HudState:
    """Read every configured field at one moment. Never raises: a field that cannot be read
    is None, because a HUD must not be able to fail a review."""
    stem = f"state_{timestamp_s:.1f}".replace(".", "_")
    numbers: dict[str, int | None] = {}
    errors: list[str] = []
    for name in ("health", "credits"):
        region = regions.get(name)
        if region is None:
            numbers[name] = None
            continue
        gray = _crop(recording, timestamp_s, region, runner, out_dir / stem / f"{name}.pgm")
        if gray is None:
            numbers[name] = None
            errors.append(f"{name} crop failed")
            continue
        numbers[name] = read_number(gray, templates, min_confidence, threshold)

    lit: list[bool] = []
    for i, region in enumerate(ability_regions):
        gray = _crop(recording, timestamp_s, region, runner, out_dir / stem / f"ability{i}.pgm")
        if gray is None:
            errors.append(f"ability {i} crop failed")
            continue
        lit.append(lit_fraction(gray, lit_threshold) >= lit_min_fraction)

    return HudState(numbers["health"], numbers["credits"], tuple(lit), tuple(errors))


def find_deaths(
    samples: Sequence[tuple[float, int | None]],
    missing_gap_s: float = DEFAULT_MISSING_GAP_S,
) -> tuple[float, ...]:
    """Timestamps where the player died, from a health scan.

    Two signals, because neither is enough alone. A readable 0 is unambiguous but only
    shows for an instant. The player's own HUD then disappears, so a sustained run of
    unreadable samples after a live reading means the same thing; a brief one is somebody
    walking in front of the number.
    """
    deaths: list[float] = []
    alive = False
    missing_since: float | None = None
    for timestamp, health in samples:
        if health is None:
            if alive and missing_since is None:
                missing_since = timestamp
            continue
        if missing_since is not None:
            missing_since = None
        if health <= 0:
            if alive:
                deaths.append(timestamp)
            alive = False
        else:
            alive = True

    # A run of unreadable samples that never recovers is a death at the moment it started.
    if (
        alive
        and missing_since is not None
        and samples
        and samples[-1][0] - missing_since >= missing_gap_s
    ):
        deaths.append(missing_since)
    return tuple(sorted(deaths))
