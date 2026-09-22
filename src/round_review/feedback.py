"""Whether a finding was any good, according to the person it was about.

Everything else in this project is an argument about what the model probably got right.
This is the only place that measures it. Two things come out of it: a hit rate, so the
quality of a model or a prompt change can be compared against the last one instead of
judged on a screenshot; and a per-check dismissal rate, so a check the player keeps
throwing out can stop being surfaced first.

Append-only JSONL beside the ledger, for the same reason the ledger is: a half-written
line loses one opinion rather than the file.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Verdict = Literal["useful", "wrong"]
VERDICTS: frozenset[str] = frozenset({"useful", "wrong"})


@dataclass(frozen=True, slots=True)
class FeedbackEntry:
    key: str
    check_id: str
    timestamp_s: float
    verdict: str
    noted_at: datetime

    @property
    def moment(self) -> tuple[str, str, float]:
        """What identifies one opinion: this finding, in this clip, at this moment."""
        return (self.key, self.check_id, self.timestamp_s)


def record_feedback(path: Path, entry: FeedbackEntry) -> None:
    if entry.verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VERDICTS)}, got {entry.verdict!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "key": entry.key,
            "check_id": entry.check_id,
            "timestamp_s": entry.timestamp_s,
            "verdict": entry.verdict,
            "noted_at": entry.noted_at.isoformat(),
        }
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_feedback(path: Path, latest_only: bool = False) -> tuple[FeedbackEntry, ...]:
    """Every opinion, oldest first. A corrupt line is skipped: one bad write must not cost
    the rest of the history."""
    if not path.exists():
        return ()
    entries: list[FeedbackEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            entries.append(
                FeedbackEntry(
                    key=str(raw["key"]),
                    check_id=str(raw["check_id"]),
                    timestamp_s=float(raw["timestamp_s"]),
                    verdict=str(raw["verdict"]),
                    noted_at=datetime.fromisoformat(str(raw["noted_at"])),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    if not latest_only:
        return tuple(entries)
    # Later lines win, so changing your mind is an append rather than a rewrite.
    newest = {entry.moment: entry for entry in entries}
    return tuple(newest.values())


def hit_rate(entries: Sequence[FeedbackEntry]) -> float | None:
    """Share of rated findings the player called useful, or None when nothing is rated."""
    if not entries:
        return None
    return sum(1 for e in entries if e.verdict == "useful") / len(entries)


def dismissal_rate(entries: Sequence[FeedbackEntry], min_ratings: int = 1) -> dict[str, float]:
    """Per check, the share of its findings the player threw out.

    `min_ratings` guards the ranking against one bad day: a check dismissed once is not
    evidence the check is wrong, it is evidence of one finding being wrong.
    """
    totals: dict[str, int] = {}
    wrong: dict[str, int] = {}
    for entry in entries:
        totals[entry.check_id] = totals.get(entry.check_id, 0) + 1
        if entry.verdict == "wrong":
            wrong[entry.check_id] = wrong.get(entry.check_id, 0) + 1
    return {
        check: wrong.get(check, 0) / total
        for check, total in totals.items()
        if total >= min_ratings
    }


# A check the player throws out more often than not stops leading the report. Never
# suppressed outright: the coaching may be right and the player may not want to hear it.
MOSTLY_WRONG = 0.5
MIN_RATINGS_TO_ACT = 3
