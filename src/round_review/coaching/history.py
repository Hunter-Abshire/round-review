"""What the player did in previous matches, so a habit can be called a habit.

A report that says the same thing every week without noticing it is saying the same thing
every week is not coaching. Marking a habit as new, repeating, improving or persistent is
what separates a coach who remembers you from a stranger watching one clip, and it is the
one thing in the report that no single-match review can produce.

Rates, not counts: a 25-round match and a 9-round Swiftplay are not comparable, and the
naive comparison would congratulate the player for playing a shorter game.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

HabitTrend = Literal["new", "repeat", "improving", "persistent"]

# How many previous matches a trend looks at. Far enough back to see a habit, near enough
# that a month-old problem the player has already fixed does not haunt the report.
DEFAULT_LOOKBACK = 5
# A habit in every one of this many recent matches is persistent rather than merely repeat.
PERSISTENT_MATCHES = 3
# The rate must fall to this share of its previous average to count as improving.
IMPROVING_RATIO = 0.6


@dataclass(frozen=True, slots=True)
class MatchHabits:
    """One reviewed match, reduced to how often each check fired."""

    key: str
    reviewed_at: datetime
    windows: int
    counts: Mapping[str, int]

    def rate(self, check_id: str) -> float:
        return self.counts.get(check_id, 0) / self.windows if self.windows else 0.0


def read_history(path: Path) -> tuple[MatchHabits, ...]:
    """Past matches, oldest first. A missing or corrupt file is simply no history: this is
    a nicety on top of a review and must never be able to fail one."""
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))["matches"]
        return tuple(
            MatchHabits(
                key=str(item["key"]),
                reviewed_at=datetime.fromisoformat(str(item["reviewed_at"])),
                windows=int(item["windows"]),
                counts={str(k): int(v) for k, v in dict(item["counts"]).items()},
            )
            for item in raw
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return ()


def append_match(path: Path, record: MatchHabits) -> None:
    """Record this match, replacing any earlier record for the same clip so a re-review
    does not count as a second match."""
    history = [m for m in read_history(path) if m.key != record.key]
    history.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "matches": [
            {
                "key": m.key,
                "reviewed_at": m.reviewed_at.isoformat(),
                "windows": m.windows,
                "counts": dict(m.counts),
            }
            for m in history
        ]
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def tag_habits(
    counts: Mapping[str, int],
    history: Sequence[MatchHabits],
    windows: int,
    lookback: int = DEFAULT_LOOKBACK,
) -> dict[str, HabitTrend]:
    """Label each of this match's checks against the recent ones."""
    recent = [m for m in history[-lookback:] if m.windows > 0]
    tags: dict[str, HabitTrend] = {}
    for check_id, count in counts.items():
        seen = [m for m in recent if m.counts.get(check_id)]
        if not seen:
            tags[check_id] = "new"
            continue
        now = count / windows if windows else 0.0
        before = sum(m.rate(check_id) for m in seen) / len(seen)
        if before > 0 and now <= before * IMPROVING_RATIO:
            tags[check_id] = "improving"
        elif len(seen) == len(recent) and len(seen) >= PERSISTENT_MATCHES:
            tags[check_id] = "persistent"
        else:
            tags[check_id] = "repeat"
    return tags
