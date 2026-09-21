"""Bundled coaching knowledge: the review checklist, agent briefs and map briefs.

The JSON files next to this module are data, not code. They are loaded once, validated,
and rendered into prompt text. Keep every string short: all of it goes into the model's
context window.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from round_review.errors import KnowledgeError

ROLES: frozenset[str] = frozenset({"duelist", "initiator", "controller", "sentinel"})

RANK_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("iron_bronze", ("iron", "bronze")),
    ("silver_gold", ("silver", "gold")),
    ("platinum_diamond", ("platinum", "plat", "diamond")),
    ("ascendant_plus", ("ascendant", "immortal", "radiant")),
)


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    check: str
    common_mistake: str
    fix: str
    visible_in_frames: bool


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    principle: str
    checks: tuple[Check, ...]


@dataclass(frozen=True, slots=True)
class Checklist:
    categories: tuple[Category, ...]
    rank_expectations: Mapping[str, str]

    def check_ids(self) -> frozenset[str]:
        return frozenset(c.id for cat in self.categories for c in cat.checks)

    def find_check(self, check_id: str) -> Check | None:
        return next((c for cat in self.categories for c in cat.checks if c.id == check_id), None)


@dataclass(frozen=True, slots=True)
class AgentBrief:
    id: str
    name: str
    role: str
    abilities: tuple[tuple[str, str, str], ...]  # (key, name, purpose)
    job_in_round: str
    good_play_looks_like: tuple[str, ...]
    common_mistakes: tuple[str, ...]
    ability_checks: tuple[str, ...]
    tips: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MapBrief:
    id: str
    name: str
    in_competitive_rotation: bool
    sites: tuple[str, ...]
    layout_summary: str
    key_callouts: tuple[str, ...]
    attack_defaults: tuple[str, ...]
    defense_setups: tuple[str, ...]
    power_positions: tuple[str, ...]
    common_mistakes: tuple[str, ...]
    utility_notes: tuple[str, ...]


# -- parsing ---------------------------------------------------------------------------------


def _field(obj: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise KnowledgeError(f"{where}: missing field {key!r}")
    return obj[key]


def _strings(obj: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = _field(obj, key, where)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise KnowledgeError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def parse_checklist(data: Mapping[str, Any]) -> Checklist:
    categories: list[Category] = []
    seen: set[str] = set()
    for raw_cat in _field(data, "categories", "checklist"):
        cat_id = str(_field(raw_cat, "id", "category"))
        where = f"category {cat_id}"
        checks: list[Check] = []
        for raw in _field(raw_cat, "checks", where):
            check_id = str(_field(raw, "id", where))
            if not check_id.startswith(f"{cat_id}."):
                raise KnowledgeError(f"{where}: check id {check_id!r} lacks the category prefix")
            if check_id in seen:
                raise KnowledgeError(f"{where}: duplicate check id {check_id!r}")
            seen.add(check_id)
            checks.append(
                Check(
                    id=check_id,
                    check=str(_field(raw, "check", check_id)),
                    common_mistake=str(_field(raw, "common_mistake", check_id)),
                    fix=str(_field(raw, "fix", check_id)),
                    visible_in_frames=bool(_field(raw, "visible_in_frames", check_id)),
                )
            )
        categories.append(
            Category(
                id=cat_id,
                name=str(_field(raw_cat, "name", where)),
                principle=str(_field(raw_cat, "principle", where)),
                checks=tuple(checks),
            )
        )
    ranks = _field(data, "rank_expectations", "checklist")
    if not isinstance(ranks, Mapping):
        raise KnowledgeError("checklist: rank_expectations must be an object")
    return Checklist(tuple(categories), {str(k): str(v) for k, v in ranks.items()})


def parse_agents(data: Mapping[str, Any]) -> dict[str, AgentBrief]:
    agents: dict[str, AgentBrief] = {}
    for raw in _field(data, "agents", "agents"):
        agent_id = str(_field(raw, "id", "agent"))
        where = f"agent {agent_id}"
        role = str(_field(raw, "role", where))
        if role not in ROLES:
            raise KnowledgeError(f"{where}: unknown role {role!r}")
        abilities = tuple(
            (
                str(_field(a, "key", where)),
                str(_field(a, "name", where)),
                str(_field(a, "purpose", where)),
            )
            for a in _field(raw, "abilities", where)
        )
        if agent_id in agents:
            raise KnowledgeError(f"{where}: duplicate agent id")
        agents[agent_id] = AgentBrief(
            id=agent_id,
            name=str(_field(raw, "name", where)),
            role=role,
            abilities=abilities,
            job_in_round=str(_field(raw, "job_in_round", where)),
            good_play_looks_like=_strings(raw, "good_play_looks_like", where),
            common_mistakes=_strings(raw, "common_mistakes", where),
            ability_checks=_strings(raw, "ability_checks", where),
            tips=_strings(raw, "tips", where),
        )
    return agents


def parse_maps(data: Mapping[str, Any]) -> dict[str, MapBrief]:
    maps: dict[str, MapBrief] = {}
    for raw in _field(data, "maps", "maps"):
        map_id = str(_field(raw, "id", "map"))
        where = f"map {map_id}"
        if map_id in maps:
            raise KnowledgeError(f"{where}: duplicate map id")
        maps[map_id] = MapBrief(
            id=map_id,
            name=str(_field(raw, "name", where)),
            in_competitive_rotation=bool(_field(raw, "in_competitive_rotation", where)),
            sites=_strings(raw, "sites", where),
            layout_summary=str(_field(raw, "layout_summary", where)),
            key_callouts=_strings(raw, "key_callouts", where),
            attack_defaults=_strings(raw, "attack_defaults", where),
            defense_setups=_strings(raw, "defense_setups", where),
            power_positions=_strings(raw, "power_positions", where),
            common_mistakes=_strings(raw, "common_mistakes", where),
            utility_notes=_strings(raw, "utility_notes", where),
        )
    return maps


# -- loading ---------------------------------------------------------------------------------


def _read(name: str) -> Any:
    try:
        return json.loads(files(__package__).joinpath(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeError(f"{name}: cannot load: {exc}") from exc


@cache
def load_checklist() -> Checklist:
    return parse_checklist(_read("checklist.json"))


@cache
def load_agents() -> dict[str, AgentBrief]:
    return parse_agents(_read("agents.json"))


@cache
def load_maps() -> dict[str, MapBrief]:
    return parse_maps(_read("maps.json"))


@dataclass(frozen=True, slots=True)
class CoachingKnowledge:
    checklist: Checklist
    agents: Mapping[str, AgentBrief]
    maps: Mapping[str, MapBrief]


@cache
def load_knowledge() -> CoachingKnowledge:
    return CoachingKnowledge(load_checklist(), load_agents(), load_maps())


# -- lookup ----------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_agent(agents: Mapping[str, AgentBrief], name: str | None) -> AgentBrief | None:
    if not name:
        return None
    wanted = _slug(name)
    return next(
        (a for a in agents.values() if _slug(a.id) == wanted or _slug(a.name) == wanted), None
    )


def find_map(maps: Mapping[str, MapBrief], name: str | None) -> MapBrief | None:
    if not name:
        return None
    wanted = _slug(name)
    return next(
        (m for m in maps.values() if _slug(m.id) == wanted or _slug(m.name) == wanted), None
    )


# -- phase relevance -------------------------------------------------------------------------

CORE_CATEGORIES: frozenset[str] = frozenset(
    {
        "crosshair",
        "movement",
        "peeking",
        "positioning",
        "utility",
        "trading",
        "info",
        "timing",
        "mental",
    }
)
PHASE_CATEGORIES: dict[str, frozenset[str]] = {
    "pre_round": frozenset({"economy", "info", "mental", "positioning", "utility"}),
    "early": CORE_CATEGORIES,
    "mid": CORE_CATEGORIES,
    "post_plant": CORE_CATEGORIES | {"postplant"},
    "retake": CORE_CATEGORIES | {"retake"},
}


def relevant_categories(phase: str | None) -> frozenset[str] | None:
    """Checklist categories worth sending for a round phase; None means send everything.
    Keeps the coach prompt small enough to fit next to a dozen images."""
    if phase is None:
        return None
    return PHASE_CATEGORIES.get(phase)


# -- rendering for prompts -------------------------------------------------------------------


def render_checklist(
    checklist: Checklist,
    visible_only: bool = False,
    categories: frozenset[str] | None = None,
) -> str:
    """Compact: one header line per category, one line per check with its id."""
    lines: list[str] = []
    for cat in checklist.categories:
        if categories is not None and cat.id not in categories:
            continue
        checks = [c for c in cat.checks if c.visible_in_frames or not visible_only]
        if not checks:
            continue
        lines.append(f"{cat.name}: {cat.principle}")
        lines.extend(f"  - [{c.id}] {c.check}" for c in checks)
    return "\n".join(lines)


def _bullets(title: str, items: Sequence[str]) -> list[str]:
    return [f"{title}:", *[f"  - {i}" for i in items]] if items else []


def render_agent_brief(agent: AgentBrief) -> str:
    lines = [f"Agent brief: {agent.name} ({agent.role}). {agent.job_in_round}"]
    lines += _bullets("Abilities", [f"{k} {n}: {p}" for k, n, p in agent.abilities])
    lines += _bullets("Good play looks like", agent.good_play_looks_like)
    lines += _bullets("Common mistakes to look for", agent.common_mistakes)
    lines += _bullets("HUD ability checks", agent.ability_checks)
    return "\n".join(lines)


def render_map_brief(m: MapBrief) -> str:
    lines = [f"Map brief: {m.name} (sites {', '.join(m.sites)}). {m.layout_summary}"]
    lines += _bullets("Key callouts", m.key_callouts)
    lines += _bullets("Attack defaults", m.attack_defaults)
    lines += _bullets("Defense setups", m.defense_setups)
    lines += _bullets("Power positions", m.power_positions)
    lines += _bullets("Common mistakes to look for", m.common_mistakes)
    lines += _bullets("Utility notes", m.utility_notes)
    return "\n".join(lines)


def render_rank_focus(checklist: Checklist, rank: str | None) -> str | None:
    if not rank:
        return None
    lowered = rank.lower()
    for bucket, names in RANK_BUCKETS:
        if any(n in lowered for n in names):
            return checklist.rank_expectations.get(bucket)
    return None
