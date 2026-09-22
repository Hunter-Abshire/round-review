"""Choosing which frames each pass actually needs.

The situation pass runs for every window, including the ones it then skips, so the images it
carries dominate the cost of a review: a dozen 1280px frames is tens of thousands of image
tokens per window before any coaching happens. It only has to identify the scene, which a
few frames spanning the window do as well as all of them.
"""

from __future__ import annotations

from collections.abc import Sequence

from round_review.video.frames import FrameSample


def select_situation_frames(samples: Sequence[FrameSample], count: int) -> list[FrameSample]:
    """`count` frames spread evenly across the window, endpoints included.

    A count of 1 takes the middle frame, as the most representative single moment. A count
    of 0 or less keeps every frame, for anyone who would rather pay for the detail.
    """
    if not samples:
        return []
    if count <= 0 or count >= len(samples):
        return list(samples)
    if count == 1:
        return [samples[len(samples) // 2]]
    last = len(samples) - 1
    return [samples[round(i * last / (count - 1))] for i in range(count)]
