"""Pure window selection: which slices of a recording get reviewed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

WindowSource = Literal["evenly_spaced", "tiled", "events"]
Coverage = Literal["full", "sampled"]

# A trailing tile shorter than this fraction of a window is dropped rather than reviewed.
MIN_TAIL_FRACTION = 0.5


@dataclass(frozen=True, slots=True)
class Window:
    index: int
    start_s: float
    end_s: float
    source: WindowSource


def select_windows(
    duration_s: float,
    window_s: float,
    count: int,
    edge_skip_s: float,
    max_span_s: float = 0.0,
) -> list[Window]:
    """Pick up to `count` non-overlapping windows of `window_s` seconds, evenly spread.

    The first and last `edge_skip_s` seconds are avoided (loading/end screens). If the
    recording is too short for that, a single window is centred instead; if it is shorter
    than one window, the whole recording is the window.
    """
    if duration_s <= 0 or window_s <= 0 or count <= 0:
        raise ValueError("duration_s, window_s and count must be > 0")

    if duration_s <= window_s:
        return [Window(0, 0.0, duration_s, "evenly_spaced")]

    usable_start, usable_end = _usable_span(duration_s, edge_skip_s, max_span_s)
    usable_len = usable_end - usable_start
    if usable_len < window_s:
        start = max(0.0, (duration_s - window_s) / 2)
        return [Window(0, start, min(duration_s, start + window_s), "evenly_spaced")]

    n = min(count, math.floor(usable_len / window_s))
    if n == 1:
        start = usable_start + (usable_len - window_s) / 2
        return [Window(0, start, start + window_s, "evenly_spaced")]

    step = (usable_len - window_s) / (n - 1)
    return [
        Window(i, usable_start + i * step, usable_start + i * step + window_s, "evenly_spaced")
        for i in range(n)
    ]


def _usable_span(duration_s: float, edge_skip_s: float, max_span_s: float) -> tuple[float, float]:
    """The part of the recording worth reviewing: intro and outro skipped, optionally capped.

    `max_span_s` is how many seconds of gameplay to review measured from the end of the
    intro skip, so "first minute" means the first minute after the loading screen. The
    returned span can be empty; callers decide what that means for their sampling mode.
    """
    start = edge_skip_s
    end = duration_s - edge_skip_s
    if max_span_s > 0:
        end = min(end, start + max_span_s)
    return start, end


def tile_windows(
    duration_s: float,
    window_s: float,
    edge_skip_s: float,
    max_windows: int = 0,
    max_span_s: float = 0.0,
) -> list[Window]:
    """Contiguous windows covering the whole usable span: a full review of the recording.

    `max_windows` (0 = unlimited) caps the review budget. When the budget is smaller than
    full coverage the windows are spread evenly across the span instead of stopping early,
    so the review still covers the whole recording.
    """
    if duration_s <= 0 or window_s <= 0 or max_windows < 0 or max_span_s < 0:
        raise ValueError("duration_s and window_s must be > 0; caps must not be negative")

    if duration_s <= window_s:
        return [Window(0, 0.0, duration_s, "tiled")]

    start, end = _usable_span(duration_s, edge_skip_s, max_span_s)
    if end - start < window_s and edge_skip_s > 0:
        # The skips leave too little to tile: a full review means the whole recording.
        start, end = _usable_span(duration_s, 0.0, max_span_s)
    span = end - start
    if span < window_s:
        centred = max(0.0, (duration_s - window_s) / 2)
        return [Window(0, centred, min(duration_s, centred + window_s), "tiled")]

    tiles = math.floor(span / window_s)
    tail = span - tiles * window_s
    if tail >= window_s * MIN_TAIL_FRACTION:
        tiles += 1

    if max_windows and tiles > max_windows:
        return select_windows(duration_s, window_s, max_windows, edge_skip_s, max_span_s)

    windows: list[Window] = []
    for i in range(tiles):
        tile_start = start + i * window_s
        windows.append(Window(i, tile_start, min(end, tile_start + window_s), "tiled"))
    return windows


def plan_windows(
    duration_s: float,
    *,
    window_s: float,
    coverage: Coverage,
    windows_per_file: int,
    edge_skip_s: float,
    max_windows: int = 0,
    max_span_s: float = 0.0,
) -> list[Window]:
    """The single entry point the pipeline uses to decide what gets reviewed."""
    if coverage == "full":
        return tile_windows(duration_s, window_s, edge_skip_s, max_windows, max_span_s)
    if coverage == "sampled":
        return select_windows(duration_s, window_s, windows_per_file, edge_skip_s, max_span_s)
    raise ValueError(f"unknown coverage mode {coverage!r}; expected 'full' or 'sampled'")


def estimate_window_count(
    duration_s: float,
    *,
    window_s: float,
    coverage: Coverage,
    windows_per_file: int,
    edge_skip_s: float,
    max_windows: int = 0,
    max_span_s: float = 0.0,
) -> int:
    """How many windows a review of this recording would produce, for UI estimates."""
    return len(
        plan_windows(
            duration_s,
            window_s=window_s,
            coverage=coverage,
            windows_per_file=windows_per_file,
            edge_skip_s=edge_skip_s,
            max_windows=max_windows,
            max_span_s=max_span_s,
        )
    )
