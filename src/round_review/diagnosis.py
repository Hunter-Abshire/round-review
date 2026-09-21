"""Why a review produced nothing.

A review that abstains on every window looks identical to a review of flawless play. The
difference matters: on a small local model, "every window was buy phase" almost always
means the model misread the screen, not that the player did nothing worth coaching.
"""

from __future__ import annotations

from collections.abc import Sequence

from round_review.coaching.review import WindowResult

# Abstaining on this reason is a real answer about the footage, not a sign of a bad read.
BENIGN_REASONS: frozenset[str] = frozenset({"spectating another player"})

# Below this share of abstained windows the review is simply a quiet one.
DOMINANT_SHARE = 0.6


def abstention_warning(results: Sequence[WindowResult]) -> str | None:
    """A file-level warning when abstentions dominate the review, or None."""
    if not results:
        return None
    abstained = [r for r in results if r.abstained]
    findings = sum(len(r.findings) for r in results)
    if not abstained:
        return None
    share = len(abstained) / len(results)
    if findings > 0 and share < DOMINANT_SHARE:
        return None

    counts: dict[str, int] = {}
    for result in abstained:
        reason = result.abstained_reason or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    breakdown = ", ".join(
        f"{n} {reason}" for reason, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    summary = (
        f"{len(abstained)} of {len(results)} reviewed windows were skipped before coaching "
        f"({breakdown})"
    )
    if set(counts) <= BENIGN_REASONS:
        return f"{summary}. Nothing of your own play was on screen in those windows."
    return (
        f"{summary}, so this review has little or nothing to say. That usually means the "
        "model is misreading the screen rather than that the play was clean. Check it with "
        "`round-review scenes validate` before trusting this report, and try a larger model."
    )
