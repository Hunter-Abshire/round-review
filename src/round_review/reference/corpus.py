"""The searchable corpus: the bundled knowledge, plus whatever the player writes down.

A review prompt can only carry the one agent and the one map in play. A question deserves
more: a question about a Viper wall on Bind should reach the Viper brief, the Bind callouts
and the player's own lineup notes, whatever the situation pass happened to identify.

The notes folder is the part that makes nuanced questions work. Drop markdown or text files
in it and their sections become retrievable alongside the bundled briefs, so the coach can
answer from your team's calls and your own lineups rather than only from what shipped.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from round_review.coaching.knowledge import (
    AgentBrief,
    CoachingKnowledge,
    MapBrief,
    load_drills,
    render_agent_brief,
    render_map_brief,
)
from round_review.errors import KnowledgeError

PASSAGE_KINDS: tuple[str, ...] = ("agent", "map", "check", "drill", "note")
NOTE_SUFFIXES: frozenset[str] = frozenset({".md", ".txt", ".markdown"})
MAX_NOTE_BYTES = 1024 * 1024
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


@dataclass(frozen=True, slots=True)
class Passage:
    id: str
    kind: str
    title: str
    text: str
    tags: tuple[str, ...] = ()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _agent_passage(agent: AgentBrief) -> Passage:
    return Passage(
        id=f"agent:{agent.id}",
        kind="agent",
        title=f"{agent.name} ({agent.role})",
        text=render_agent_brief(agent),
        tags=(agent.id, agent.role),
    )


def _map_passage(game_map: MapBrief) -> Passage:
    return Passage(
        id=f"map:{game_map.id}",
        kind="map",
        title=game_map.name,
        text=render_map_brief(game_map),
        tags=(game_map.id,),
    )


def build_corpus(
    knowledge: CoachingKnowledge, notes: Sequence[Passage] | None = None
) -> list[Passage]:
    """Every retrievable passage: one per agent, map, check and category drill, plus notes."""
    passages: list[Passage] = [
        _agent_passage(agent) for agent in sorted(knowledge.agents.values(), key=lambda a: a.id)
    ]
    passages += [
        _map_passage(game_map) for game_map in sorted(knowledge.maps.values(), key=lambda m: m.id)
    ]
    for category in knowledge.checklist.categories:
        for check in category.checks:
            passages.append(
                Passage(
                    id=f"check:{check.id}",
                    kind="check",
                    title=f"{category.name}: {check.id}",
                    text=(
                        f"{category.principle} What to look for: {check.check} "
                        f"Common mistake: {check.common_mistake} Fix: {check.fix}"
                    ),
                    tags=(category.id,),
                )
            )
    for drill_category, practice in sorted(load_drills().by_category.items()):
        passages.append(
            Passage(
                id=f"drill:{drill_category}",
                kind="drill",
                title=f"{drill_category} practice",
                text=f"In-game rule: {practice.rule} Drill: {practice.drill}",
                tags=(drill_category,),
            )
        )
    passages += list(notes or [])
    return passages


def _sections(text: str, fallback_title: str) -> list[tuple[str, str]]:
    """Split markdown on headings. A file with no headings is one section."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        heading = HEADING_RE.match(line.strip())
        if heading:
            sections.append((heading.group(1).strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            sections.append((fallback_title, [line]))
    return [(title, "\n".join(body).strip()) for title, body in sections if "".join(body).strip()]


def _tags_for(text: str) -> tuple[str, ...]:
    """Tag a note with any agent or map it names, so it is favoured in the right clip."""
    from round_review.coaching.knowledge import load_agents, load_maps

    haystack = _slug(text)
    found = [key for key in load_agents() if key and key in haystack]
    found += [key for key in load_maps() if key and key in haystack]
    return tuple(sorted(set(found)))


def load_notes(directory: Path | None) -> list[Passage]:
    """Read the player's own notes. A missing folder is simply no notes."""
    if directory is None or not directory.is_dir():
        return []
    passages: list[Passage] = []
    for path in sorted(p for p in directory.rglob("*") if p.suffix.lower() in NOTE_SUFFIXES):
        try:
            if path.stat().st_size > MAX_NOTE_BYTES:
                raise KnowledgeError(
                    f"{path}: note file is too large ({path.stat().st_size // 1024} KB); "
                    f"split it into files under {MAX_NOTE_BYTES // 1024} KB"
                )
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise KnowledgeError(f"{path}: cannot read note: {exc}") from exc
        for title, body in _sections(raw, path.stem):
            digest = hashlib.sha256(f"{path}|{title}|{body[:80]}".encode()).hexdigest()[:10]
            passages.append(
                Passage(
                    id=f"note:{digest}",
                    kind="note",
                    title=title,
                    text=body,
                    tags=_tags_for(f"{title} {body}"),
                )
            )
    return passages
