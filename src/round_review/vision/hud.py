"""Deterministic HUD reading: the round clock, and what it proves about the round phase.

A vision model can call live play "buy phase". The round clock cannot: Valorant's buy phase
and its post-plant spike timer both run from well under a minute, so a clock above that
threshold is proof the round is live and pre-plant. That single fact is enough to veto the
misreads that make a whole review abstain, and it costs one ffmpeg crop per window.

Screen regions are normalised (0..1) so they survive any resolution, and configurable
because HUD layouts move between game versions. Verify yours with `round-review hud crop`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from round_review.errors import HudError, RoundReviewError
from round_review.video.probe import CommandRunner, Recording
from round_review.vision.digits import DigitTemplates, parse_clock, read_text
from round_review.vision.raster import Glyph, normalize_glyph, parse_pgm, segment_glyphs

# The crop is upscaled before thresholding so thin strokes survive.
CROP_SCALE = 4
# Above this the round is live and pre-plant: neither the buy phase nor the spike timer
# ever shows a clock this high.
DEFAULT_BUY_PHASE_MAX_S = 45.0
# A round starts at 1:40, so a clock near that is the opening of the round.
EARLY_ROUND_S = 80.0

# Phases that can only happen while the clock is below the buy-phase maximum.
CLOCK_BOUNDED_PHASES: frozenset[str] = frozenset({"pre_round", "post_plant", "retake"})
# Spectating shows someone else's clock, so the clock proves nothing about the player.
NEVER_OVERRIDDEN: frozenset[str] = frozenset({"spectating"})


@dataclass(frozen=True, slots=True)
class Region:
    """A rectangle of the frame, as fractions of its width and height."""

    x: float
    y: float
    width: float
    height: float

    def in_pixels(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        return (
            int(self.x * frame_width),
            int(self.y * frame_height),
            int(self.width * frame_width),
            int(self.height * frame_height),
        )


@dataclass(frozen=True, slots=True)
class HudRead:
    clock_text: str | None
    clock_s: float | None
    confidence: float
    glyph_count: int
    crop_path: Path | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseVerdict:
    phase: str | None
    overridden: bool
    reason: str | None = None


def parse_region(text: str) -> Region:
    """Parse "x,y,w,h" as fractions of the frame."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise HudError(f"region must be 'x,y,w,h' as fractions of the frame, got {text!r}")
    try:
        x, y, width, height = (float(p) for p in parts)
    except ValueError as exc:
        raise HudError(f"region {text!r} is not four numbers: {exc}") from exc
    if width <= 0 or height <= 0:
        raise HudError(f"region {text!r} has no area")
    if x < 0 or y < 0 or x + width > 1 or y + height > 1:
        raise HudError(f"region {text!r} falls outside the frame")
    return Region(x, y, width, height)


def build_crop_args(
    path: Path,
    timestamp_s: float,
    region: Region,
    recording: Recording,
    out_path: Path,
    scale: int = CROP_SCALE,
) -> list[str]:
    x, y, width, height = region.in_pixels(recording.width, recording.height)
    return [
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        (
            f"crop={width}:{height}:{x}:{y},"
            f"scale={width * scale}:{height * scale}:flags=lanczos,format=gray"
        ),
        str(out_path),
    ]


def read_hud(
    recording: Recording,
    timestamp_s: float,
    region: Region,
    runner: CommandRunner,
    templates: DigitTemplates,
    out_dir: Path,
    min_confidence: float,
    threshold: int = -1,
) -> HudRead:
    """Crop the clock region and read it. Never raises: a HUD that cannot be read is a read
    with an error, because it must not be able to fail a review."""
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_path = out_dir / f"hud_{timestamp_s:.1f}".replace(".", "_") / "timer.pgm"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        runner.run(build_crop_args(recording.path, timestamp_s, region, recording, crop_path))
        gray = parse_pgm(crop_path.read_bytes())
    except (RoundReviewError, OSError) as exc:
        return HudRead(None, None, 0.0, 0, crop_path, f"{type(exc).__name__}: {exc}")

    boxes = segment_glyphs(gray, threshold=threshold, min_gap=1, min_pixels=3)
    glyphs = [
        normalize_glyph(gray, b.x, b.y, b.width, b.height, threshold=threshold) for b in boxes
    ]
    text, confidence = read_text(glyphs, templates, min_confidence)
    return HudRead(text, parse_clock(text), confidence, len(glyphs), crop_path)


def learn_from_crop(
    recording: Recording,
    timestamp_s: float,
    region: Region,
    runner: CommandRunner,
    out_dir: Path,
    reads: str,
    threshold: int = -1,
) -> list[tuple[str, Glyph]]:
    """Teach the templates one crop: segment it and pair each glyph with the character the
    player says it is. The glyph count must match, otherwise the pairing would be wrong."""
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_path = out_dir / f"learn_{timestamp_s:.1f}".replace(".", "_") / "timer.pgm"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    runner.run(build_crop_args(recording.path, timestamp_s, region, recording, crop_path))
    gray = parse_pgm(crop_path.read_bytes())
    boxes = segment_glyphs(gray, threshold=threshold, min_gap=1, min_pixels=3)
    if len(boxes) != len(reads):
        raise HudError(
            f"the crop at t={timestamp_s:.1f}s has {len(boxes)} glyph(s) but you said it reads "
            f"{reads!r} ({len(reads)} character(s)). Check {crop_path.parent} and adjust the "
            "region with --region, or pick a cleaner timestamp."
        )
    return [
        (char, normalize_glyph(gray, b.x, b.y, b.width, b.height, threshold=threshold))
        for char, b in zip(reads, boxes, strict=True)
    ]


def constrain_phase(
    model_phase: str | None,
    read: HudRead | None,
    buy_phase_max_s: float = DEFAULT_BUY_PHASE_MAX_S,
    min_confidence: float = 0.8,
) -> PhaseVerdict:
    """Correct the model's phase where the clock proves it wrong, and leave it otherwise.

    The only claim the clock supports on its own is "the round is live and the spike is not
    down". That is exactly the claim a misread turns into a skipped window, so it is the
    only override made here.
    """
    if read is None or read.clock_s is None or read.confidence < min_confidence:
        return PhaseVerdict(model_phase, False)
    if model_phase in NEVER_OVERRIDDEN:
        return PhaseVerdict(model_phase, False)
    if read.clock_s <= buy_phase_max_s:
        return PhaseVerdict(model_phase, False)
    if model_phase is not None and model_phase not in CLOCK_BOUNDED_PHASES:
        return PhaseVerdict(model_phase, False)

    phase = "early" if read.clock_s >= EARLY_ROUND_S else "mid"
    claimed = model_phase or "unreadable"
    return PhaseVerdict(
        phase,
        True,
        f"HUD round timer reads {read.clock_text}, above the {buy_phase_max_s:.0f}s buy phase "
        f"and spike timer, so the round is live; corrected {claimed} to {phase}",
    )
