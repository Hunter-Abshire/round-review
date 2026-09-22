"""Where the rounds are, read off the clock instead of asked of the model.

The round timer is the one thing on screen that says unambiguously "a new round just went
live": it resets to 1:40 when the barrier drops, and nothing else on the HUD counts that
high. Scanning it across the whole recording costs one ffmpeg pass and no model calls, and
it turns "t=412s" into "round 7", which is how a coach and a player both think.

Round numbers here are positions in this recording, not scoreboard round numbers: a
recording that starts mid-match starts at round 1 regardless of the score.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from round_review.errors import RoundReviewError
from round_review.video.probe import CommandRunner, Recording
from round_review.vision.digits import DigitTemplates, parse_clock, read_text
from round_review.vision.hud import CROP_SCALE, Region
from round_review.vision.raster import normalize_glyph, parse_pgm, segment_glyphs

# The round timer starts at 1:40. Nothing else on the HUD reaches this: the buy phase runs
# from 0:30 and the spike from 0:45, so a clock this high can only be a live round.
ROUND_TIMER_MIN_S = 90.0
# How far the clock must jump upwards to count as a reset rather than a misread digit.
RESET_JUMP_S = 20.0
# Two seconds is fine enough to place a barrier drop and coarse enough to stay cheap.
DEFAULT_SCAN_INTERVAL_S = 2.0


@dataclass(frozen=True, slots=True)
class ClockSample:
    timestamp_s: float
    clock_s: float | None
    confidence: float


@dataclass(frozen=True, slots=True)
class RoundSpan:
    """One round, delimited by barrier drops. `index` is 1-based within the recording."""

    index: int
    start_s: float
    end_s: float

    def contains(self, timestamp_s: float) -> bool:
        return self.start_s <= timestamp_s < self.end_s or (
            self.start_s == self.end_s and timestamp_s == self.start_s
        )


def build_scan_args(
    region: Region,
    recording: Recording,
    out_dir: Path,
    interval_s: float = DEFAULT_SCAN_INTERVAL_S,
    scale: int = CROP_SCALE,
) -> list[str]:
    """One decode of the whole file writing numbered clock crops. Per-frame `-ss` seeking
    would be hundreds of ffmpeg launches for the same result."""
    x, y, width, height = region.in_pixels(recording.width, recording.height)
    return [
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(recording.path),
        "-vf",
        (
            f"fps=1/{interval_s},"
            f"crop={width}:{height}:{x}:{y},"
            f"scale={width * scale}:{height * scale}:flags=lanczos,format=gray"
        ),
        "-vsync",
        "0",
        str(out_dir / "clock_%05d.pgm"),
    ]


def scan_timestamps(count: int, interval_s: float = DEFAULT_SCAN_INTERVAL_S) -> tuple[float, ...]:
    """Frame n of the scan came from t = (n-1) * interval."""
    return tuple(i * interval_s for i in range(count))


def scan_clock(
    recording: Recording,
    region: Region,
    runner: CommandRunner,
    templates: DigitTemplates,
    out_dir: Path,
    min_confidence: float,
    interval_s: float = DEFAULT_SCAN_INTERVAL_S,
    threshold: int = -1,
) -> tuple[ClockSample, ...]:
    """Read the clock across the whole recording. Never raises: a scan that fails is an
    empty scan, and the review falls back to time-based windows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        runner.run(build_scan_args(region, recording, out_dir, interval_s))
    except (RoundReviewError, OSError):
        return ()
    crops = sorted(out_dir.glob("clock_*.pgm"))
    times = scan_timestamps(len(crops), interval_s)
    out: list[ClockSample] = []
    for timestamp, crop in zip(times, crops, strict=True):
        try:
            gray = parse_pgm(crop.read_bytes())
        except (RoundReviewError, OSError):
            out.append(ClockSample(timestamp, None, 0.0))
            continue
        boxes = segment_glyphs(gray, threshold=threshold, min_gap=1, min_pixels=3)
        glyphs = [
            normalize_glyph(gray, b.x, b.y, b.width, b.height, threshold=threshold) for b in boxes
        ]
        text, confidence = read_text(glyphs, templates, min_confidence)
        out.append(ClockSample(timestamp, parse_clock(text), confidence))
    return tuple(out)


def segment_rounds(
    samples: tuple[ClockSample, ...], min_confidence: float = 0.8
) -> tuple[RoundSpan, ...]:
    """Split the recording at every barrier drop.

    Only a jump up to the round timer counts. The buy phase (0:30) and the spike (0:45)
    also reset the clock upwards, so requiring the new value to clear ROUND_TIMER_MIN_S is
    what keeps a planted spike from being read as a new round.
    """
    readable = [s for s in samples if s.clock_s is not None and s.confidence >= min_confidence]
    if not readable:
        return ()
    starts = [readable[0].timestamp_s]
    previous = readable[0].clock_s
    for sample in readable[1:]:
        clock = sample.clock_s
        assert clock is not None  # filtered above; narrows the type
        if previous is not None and clock >= ROUND_TIMER_MIN_S and clock - previous >= RESET_JUMP_S:
            starts.append(sample.timestamp_s)
        previous = clock
    end = readable[-1].timestamp_s
    bounds = [*starts[1:], end]
    return tuple(
        RoundSpan(i, start, max(start, stop))
        for i, (start, stop) in enumerate(zip(starts, bounds, strict=True), start=1)
    )


def round_at(spans: tuple[RoundSpan, ...], timestamp_s: float) -> RoundSpan | None:
    """The round a moment belongs to. The last span runs to the end of the recording."""
    for i, span in enumerate(spans):
        last = i == len(spans) - 1
        if span.contains(timestamp_s) or (last and timestamp_s >= span.start_s):
            return span
    return None
