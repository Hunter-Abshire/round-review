"""What the player tells us about the clip, merged with what the situation pass detects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

SIDES: frozenset[str] = frozenset({"attack", "defense"})


@dataclass(frozen=True, slots=True)
class PlayerContext:
    rank: str | None = None
    agent: str | None = None
    map: str | None = None
    side: str | None = None
    focus: str | None = None
    notes: str | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))

    def describe(self) -> str:
        parts = [
            f"{label}: {value}."
            for label, value in (
                ("Rank", self.rank),
                ("Agent", self.agent),
                ("Map", self.map),
                ("Side", self.side),
                ("Focus", self.focus),
                ("Notes", self.notes),
            )
            if value
        ]
        return " ".join(parts)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def context_from_mapping(data: Mapping[str, Any] | None) -> PlayerContext:
    data = data or {}
    side = _clean(data.get("side"))
    side = side.lower() if side else None
    return PlayerContext(
        rank=_clean(data.get("rank")),
        agent=_clean(data.get("agent")),
        map=_clean(data.get("map")),
        side=side if side in SIDES else None,
        focus=_clean(data.get("focus")),
        notes=_clean(data.get("notes")),
    )


def merge_context(user: PlayerContext, detected: PlayerContext) -> PlayerContext:
    """User-supplied values win; detected values fill the gaps."""
    changes = {
        f.name: getattr(detected, f.name)
        for f in fields(PlayerContext)
        if getattr(user, f.name) is None and getattr(detected, f.name) is not None
    }
    return replace(user, **changes)
