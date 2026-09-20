"""Pure window selection: which slices of a recording get reviewed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

WindowSource = Literal["evenly_spaced", "events"]


@dataclass(frozen=True, slots=True)
class Window:
    index: int
    start_s: float
    end_s: float
    source: WindowSource


def select_windows(
    duration_s: float, window_s: float, count: int, edge_skip_s: float
) -> list[Window]:
    """Pick up to `count` non-overlapping windows of `window_s` seconds.

    The first and last `edge_skip_s` seconds are avoided (loading/end screens). If the
    recording is too short for that, a single window is centred instead; if it is shorter
    than one window, the whole recording is the window.
    """
    if duration_s <= 0 or window_s <= 0 or count <= 0:
        raise ValueError("duration_s, window_s and count must be > 0")

    if duration_s <= window_s:
        return [Window(0, 0.0, duration_s, "evenly_spaced")]

    usable_start = edge_skip_s
    usable_len = duration_s - 2 * edge_skip_s
    if usable_len < window_s:
        start = (duration_s - window_s) / 2
        return [Window(0, start, start + window_s, "evenly_spaced")]

    n = min(count, math.floor(usable_len / window_s))
    if n == 1:
        start = usable_start + (usable_len - window_s) / 2
        return [Window(0, start, start + window_s, "evenly_spaced")]

    step = (usable_len - window_s) / (n - 1)
    return [
        Window(i, usable_start + i * step, usable_start + i * step + window_s, "evenly_spaced")
        for i in range(n)
    ]
