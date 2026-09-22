"""Pure window selection: which slices of a recording get reviewed."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # avoids a cycle: vision imports video.probe
    from round_review.vision.timeline import RoundSpan

WindowSource = Literal[
    "evenly_spaced", "tiled", "events", "asked", "round_start", "round_end", "death", "buy"
]
Coverage = Literal["full", "sampled", "rounds"]

# A trailing tile shorter than this fraction of a window is dropped rather than reviewed.
MIN_TAIL_FRACTION = 0.5
# How much of a death window sits before the death itself.
DEATH_LEAD_IN = 0.75
# Seconds after live play ends before the buy menu is up: the round-end banner runs first.
BUY_MENU_DELAY_S = 2.0


@dataclass(frozen=True, slots=True)
class Window:
    index: int
    start_s: float
    end_s: float
    source: WindowSource
    # Which round of the recording this window falls in, when the clock was readable.
    round_index: int | None = None


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


def _live_end_of(span: RoundSpan) -> float:
    return span.end_s if span.live_end_s is None else span.live_end_s


def _round_index(spans: Sequence[RoundSpan], timestamp_s: float) -> int | None:
    last = len(spans) - 1
    for i, span in enumerate(spans):
        if span.contains(timestamp_s) or (i == last and timestamp_s >= span.start_s):
            return span.index
    return None


def _clamp(start: float, window_s: float, duration_s: float) -> tuple[float, float]:
    start = min(max(0.0, start), max(0.0, duration_s - window_s))
    return start, min(duration_s, start + window_s)


def round_windows(
    spans: Sequence[RoundSpan],
    *,
    window_s: float,
    duration_s: float,
    deaths: Sequence[float] = (),
    max_windows: int = 0,
    buy_windows: bool = True,
) -> list[Window]:
    """Review the moments a coach would: the opening of each round, the seconds that ended
    it, and every death.

    Uniform tiling spends most of a review on players walking. Rounds are where decisions
    live, so with the clock read the budget goes there instead: a 15-minute game becomes
    roughly two windows per round plus deaths, not fifty-seven equal slices of wall clock.
    """
    if window_s <= 0:
        raise ValueError("window_s must be > 0")
    candidates: list[tuple[float, WindowSource, int | None]] = []
    if buy_windows:
        # The buy phase before a barrier drop belongs to the round it buys for, which is how
        # a player thinks about it. Without a window here the economy checks never ran at
        # all: credits and the team's loadout are only on screen in the buy menu.
        for previous, span in pairwise(spans):
            buy_at = _live_end_of(previous) + BUY_MENU_DELAY_S
            if buy_at + window_s <= span.start_s:
                candidates.append((buy_at, "buy", span.index))
    for span in spans:
        # The end of LIVE play, not the end of the span: a span runs barrier drop to barrier
        # drop, so its last seconds are the next round's buy phase. Anchoring here used to
        # spend half the review budget watching someone shop.
        live_end = span.end_s if span.live_end_s is None else span.live_end_s
        if live_end <= span.start_s:
            continue  # all buy phase, which a recording starting mid-shop produces
        candidates.append((span.start_s, "round_start", span.index))
        if live_end - span.start_s >= 2 * window_s:
            candidates.append((live_end - window_s, "round_end", span.index))
    # Weighted to the lead-in: the mistake that got the player killed happened before
    # the death, not after it.
    candidates.extend(
        (death - window_s * DEATH_LEAD_IN, "death", _round_index(spans, death)) for death in deaths
    )
    if not candidates:
        return []

    # Deaths first at the same moment: the death is the more specific reason to look.
    priority = {"death": 0, "buy": 1, "round_start": 2, "round_end": 3}
    candidates.sort(key=lambda c: (c[0], priority[c[1]]))

    kept: list[tuple[float, float, WindowSource, int | None]] = []
    for raw_start, source, round_index in candidates:
        start, end = _clamp(raw_start, window_s, duration_s)
        if kept and start < kept[-1][1]:
            continue  # overlaps the window already planned; reviewing it twice is waste
        kept.append((start, end, source, round_index))

    if max_windows and len(kept) > max_windows:
        step = (len(kept) - 1) / (max_windows - 1) if max_windows > 1 else 0.0
        kept = [kept[round(i * step)] for i in range(max_windows)]

    return [
        Window(i, start, end, source, round_index)
        for i, (start, end, source, round_index) in enumerate(kept)
    ]


def plan_windows(
    duration_s: float,
    *,
    window_s: float,
    coverage: Coverage,
    windows_per_file: int,
    edge_skip_s: float,
    max_windows: int = 0,
    max_span_s: float = 0.0,
    spans: Sequence[RoundSpan] = (),
    deaths: Sequence[float] = (),
) -> list[Window]:
    """The single entry point the pipeline uses to decide what gets reviewed.

    `rounds` falls back to full tiling when the clock scan found nothing, because an
    uncalibrated HUD must never turn into an empty review.
    """
    if coverage == "rounds":
        planned = round_windows(
            spans,
            window_s=window_s,
            duration_s=duration_s,
            deaths=deaths,
            max_windows=max_windows,
        )
        if planned:
            return planned
        coverage = "full"
    if coverage == "full":
        return tile_windows(duration_s, window_s, edge_skip_s, max_windows, max_span_s)
    if coverage == "sampled":
        return select_windows(duration_s, window_s, windows_per_file, edge_skip_s, max_span_s)
    raise ValueError(f"unknown coverage mode {coverage!r}; expected 'full', 'sampled' or 'rounds'")


def estimate_window_count(
    duration_s: float,
    *,
    window_s: float,
    coverage: Coverage,
    windows_per_file: int,
    edge_skip_s: float,
    max_windows: int = 0,
    max_span_s: float = 0.0,
    spans: Sequence[RoundSpan] = (),
    deaths: Sequence[float] = (),
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
            spans=spans,
            deaths=deaths,
        )
    )
