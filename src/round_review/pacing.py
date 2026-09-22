"""How long a review will take, from how long past reviews actually took.

A full review of a twelve minute recording is dozens of windows and a couple of model calls
each, which on a modest card is hours. Guessing that from hardware is hopeless; measuring it
from the player's own ledger is not.
"""

from __future__ import annotations

from collections.abc import Sequence

from round_review.ledger import LedgerEntry

# Only completed reviews are representative: a failed one stopped early.
TIMED_STATUSES: frozenset[str] = frozenset({"ok", "partial"})
RECENT_REVIEWS = 5


def measured_seconds_per_window(
    entries: Sequence[LedgerEntry], sample: int = RECENT_REVIEWS
) -> float | None:
    """Seconds per window across the most recent timed reviews, weighted by window count."""
    timed = [
        e for e in entries if e.status in TIMED_STATUSES and e.seconds_per_window() is not None
    ][-sample:]
    if not timed:
        return None
    windows = sum(e.windows for e in timed)
    return sum(e.duration_s for e in timed) / windows if windows else None


def estimate_seconds(windows: int, entries: Sequence[LedgerEntry]) -> float | None:
    if windows <= 0:
        return None
    rate = measured_seconds_per_window(entries)
    return rate * windows if rate else None


def format_duration(seconds: float) -> str:
    """A rough spoken estimate; precision here would be false confidence."""
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} minutes"
    hours, rest = divmod(minutes, 60)
    hour_word = "hour" if hours == 1 else "hours"
    if rest == 0:
        return f"about {hours} {hour_word}"
    return f"about {hours} {hour_word} {rest} minutes"
