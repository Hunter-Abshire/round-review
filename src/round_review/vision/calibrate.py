"""Choosing the HUD brightness cutoff by measuring it, not by guessing.

The adaptive cutoff picks a midpoint per crop, which moves with whatever scenery is behind
the translucent timer plate. Measured on real footage it read the clock correctly in 6 of
16 frames, while any fixed cutoff from 190 to 230 read all 16. That is not a tuning
preference, it is the difference between a HUD reader that works and one that does not.

So: give it timestamps whose clock you can read with your own eyes, and it reports which
cutoffs agree with you. The middle of the widest working run is the answer, because a
cutoff that only just works will stop working on a brighter map.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from round_review.vision.raster import Gray, segment_glyphs

# Coarse enough to stay quick, fine enough to find the edges of a working run.
DEFAULT_CANDIDATES: tuple[int, ...] = tuple(range(100, 251, 10))


@dataclass(frozen=True, slots=True)
class ThresholdScore:
    threshold: int
    exact: int
    total: int
    # (label, glyphs found) for each sample that came out the wrong length.
    misses: tuple[tuple[str, int], ...]

    @property
    def perfect(self) -> bool:
        return self.total > 0 and self.exact == self.total


def score_thresholds(
    samples: Sequence[tuple[Gray, int]],
    candidates: Sequence[int] = DEFAULT_CANDIDATES,
    labels: Sequence[str] = (),
) -> list[ThresholdScore]:
    """For each cutoff, how many crops segmented into the number of glyphs expected."""
    scored: list[ThresholdScore] = []
    for threshold in candidates:
        exact = 0
        misses: list[tuple[str, int]] = []
        for i, (gray, expected) in enumerate(samples):
            found = len(segment_glyphs(gray, threshold=threshold, min_gap=1, min_pixels=3))
            if found == expected:
                exact += 1
            else:
                misses.append((labels[i] if i < len(labels) else str(i), found))
        scored.append(ThresholdScore(threshold, exact, len(samples), tuple(misses)))
    return scored


def best_threshold(scores: Sequence[ThresholdScore]) -> int | None:
    """The middle of the widest unbroken run of cutoffs that read everything correctly.

    Taking the first one that works would sit on the edge of the cliff, and the HUD gets
    brighter on some maps.
    """
    best_run: list[int] = []
    run: list[int] = []
    for score in scores:
        if score.perfect:
            run.append(score.threshold)
            if len(run) > len(best_run):
                best_run = list(run)
        else:
            run = []
    if not best_run:
        return None
    return best_run[len(best_run) // 2]
