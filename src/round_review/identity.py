"""What a clip is: which agent, which map, and when it was played.

A folder of files called Valorant_09-21-2026_22-4-48-733.mp4 tells you nothing at a glance.
The review already works out the agent and map on its way to coaching, so recording them
costs nothing and makes the library browsable.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.review import WindowResult

# Outplayed names its files Valorant_MM-DD-YYYY_HH-MM-SS-mmm.mp4
FILENAME_RE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})_(\d{1,2})-(\d{1,2})-(\d{1,2})")


@dataclass(frozen=True, slots=True)
class ClipIdentity:
    key: str
    agent: str | None = None
    map: str | None = None
    side: str | None = None
    # Where this came from: a completed review, or a one-off look at a single frame.
    source: str = "review"


def played_at_from(path: Path, mtime: float) -> datetime | None:
    """When the clip was played, from the recorder's filename, else the file's own time."""
    match = FILENAME_RE.search(path.name)
    if match:
        month, day, year, hour, minute, second = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except ValueError:
            pass  # a name that looks like a date but is not one
    return datetime.fromtimestamp(mtime, tz=UTC) if mtime else None


def _most_common(values: Sequence[str | None]) -> str | None:
    """The answer the review settled on, so one odd window cannot rename the clip."""
    found = [value for value in values if value]
    if not found:
        return None
    return Counter(found).most_common(1)[0][0]


def identity_index(key: str, results: Sequence[WindowResult]) -> ClipIdentity | None:
    """What a finished review believes this clip was, or None if it never worked it out."""
    agent = _most_common([r.context.agent for r in results])
    game_map = _most_common([r.context.map for r in results])
    side = _most_common([r.context.side for r in results])
    if not agent and not game_map:
        return None
    return ClipIdentity(key=key, agent=agent, map=game_map, side=side, source="review")


def read_identities(path: Path) -> dict[str, ClipIdentity]:
    """Everything known so far. A corrupt file is empty rather than fatal: this is a
    convenience index, and losing it must never stop a review."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            str(key): ClipIdentity(
                key=str(key),
                agent=value.get("agent"),
                map=value.get("map"),
                side=value.get("side"),
                source=str(value.get("source", "review")),
            )
            for key, value in payload.items()
            if isinstance(value, dict)
        }
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


def write_identity(path: Path, identity: ClipIdentity) -> None:
    known = read_identities(path)
    known[identity.key] = identity
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {k: v for k, v in asdict(value).items() if k != "key"}
        for key, value in sorted(known.items())
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
