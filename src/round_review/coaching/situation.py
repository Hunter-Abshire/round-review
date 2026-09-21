"""Pass 1 output: a structured read of what is on screen, before any coaching happens.

Small vision models coach badly when asked to look and judge in one step. Asking for a
plain description first (agent, map, side, phase, HUD state, per-frame events) gives the
coach pass concrete facts to reason over and lets us auto-detect agent/map from the HUD.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from round_review.coaching.context import SIDES, PlayerContext
from round_review.errors import ParseError

PHASES: frozenset[str] = frozenset({"pre_round", "early", "mid", "post_plant", "retake", "unknown"})
UNKNOWN_VALUES: frozenset[str] = frozenset({"", "unknown", "n/a", "none", "null", "?"})


@dataclass(frozen=True, slots=True)
class Situation:
    agent: str | None
    map: str | None
    side: str | None
    phase: str | None
    weapon: str | None
    abilities_available: tuple[str, ...]
    credits: int | None
    teammates_alive: int | None
    enemies_visible: int
    timeline: tuple[tuple[float, str], ...]
    summary: str

    def to_context(self) -> PlayerContext:
        return PlayerContext(agent=self.agent, map=self.map, side=self.side)

    def describe(self) -> str:
        facts = [
            f"agent={self.agent or 'unknown'}",
            f"map={self.map or 'unknown'}",
            f"side={self.side or 'unknown'}",
            f"phase={self.phase or 'unknown'}",
            f"weapon={self.weapon or 'unknown'}",
            f"abilities available={', '.join(self.abilities_available) or 'none visible'}",
            f"credits={self.credits if self.credits is not None else 'unknown'}",
            "teammates alive="
            f"{self.teammates_alive if self.teammates_alive is not None else 'unknown'}",
            f"enemies visible={self.enemies_visible}",
        ]
        lines = ["Situation read (from pass 1): " + "; ".join(facts), f"Summary: {self.summary}"]
        if self.timeline:
            lines.append("Timeline:")
            lines.extend(f"  - t={t:.1f}s {event}" for t, event in self.timeline)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "map": self.map,
            "side": self.side,
            "phase": self.phase,
            "weapon": self.weapon,
            "abilities_available": list(self.abilities_available),
            "credits": self.credits,
            "teammates_alive": self.teammates_alive,
            "enemies_visible": self.enemies_visible,
            "timeline": [{"t": t, "event": e} for t, e in self.timeline],
            "summary": self.summary,
        }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in UNKNOWN_VALUES else text


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_situation(text: str) -> Situation:
    from round_review.coaching.parse import (
        extract_json,
    )  # local import: parse imports nothing from here

    try:
        raw = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise ParseError(f"situation reply is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ParseError("situation reply is not an object")
    if "summary" not in raw:
        raise ParseError("situation reply missing summary")

    side = _text(raw.get("side"))
    side = side.lower() if side else None
    phase = _text(raw.get("phase"))
    phase = phase.lower() if phase else None
    abilities = raw.get("abilities_available") or []
    timeline_raw = raw.get("timeline") or []
    timeline: list[tuple[float, str]] = []
    if isinstance(timeline_raw, list):
        for item in timeline_raw:
            if isinstance(item, dict) and "t" in item and "event" in item:
                try:
                    timeline.append((float(item["t"]), str(item["event"])))
                except (TypeError, ValueError):
                    continue
    return Situation(
        agent=_text(raw.get("agent")),
        map=_text(raw.get("map")),
        side=side if side in SIDES else None,
        phase=phase if phase in PHASES and phase != "unknown" else None,
        weapon=_text(raw.get("weapon")),
        abilities_available=tuple(str(a) for a in abilities) if isinstance(abilities, list) else (),
        credits=_int(raw.get("credits")),
        teammates_alive=_int(raw.get("teammates_alive")),
        enemies_visible=_int(raw.get("enemies_visible")) or 0,
        timeline=tuple(timeline),
        summary=str(raw["summary"]),
    )
