"""Bundled coaching knowledge: the review checklist, agent briefs and map briefs.

The JSON files next to this module are data, not code. They are loaded once, validated,
and rendered into prompt text. Keep every string short: all of it goes into the model's
context window.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from round_review.errors import KnowledgeError

ROLES: frozenset[str] = frozenset({"duelist", "initiator", "controller", "sentinel"})

# The feedback categories, in the order a review addresses them: what kept you alive and
# won rounds first, mechanical polish after.
CATEGORY_IDS: tuple[str, ...] = (
    "positioning",
    "trading",
    "utility",
    "timing",
    "peeking",
    "info",
    "postplant",
    "retake",
    "economy",
    "crosshair",
    "movement",
    "mental",
    "other",
)

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
class Ability:
    """One slot of a kit, with what it costs and whether that is worth paying.

    Cost is free text rather than a number: signatures are free with paid extra charges,
    Chamber sells bullets, and every one of these moves with a balance patch. A sentence
    the model can quote beats a number that will be wrong in three months.
    """

    key: str
    name: str
    purpose: str
    cost: str
    when: str
    verdict: str


@dataclass(frozen=True, slots=True)
class AgentBrief:
    id: str
    name: str
    role: str
    abilities: tuple[Ability, ...]
    # Ultimate cost in points, and whether the agent can function on a pistol round.
    ult_points: int
    eco: str
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
            Ability(
                key=str(_field(a, "key", where)),
                name=str(_field(a, "name", where)),
                purpose=str(_field(a, "purpose", where)),
                cost=str(_field(a, "cost", where)),
                when=str(_field(a, "when", where)),
                verdict=str(a.get("verdict", "")),
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
            ult_points=int(_field(raw, "ult_points", where)),
            eco=str(raw.get("eco", "")),
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
class Weapon:
    name: str
    cost: int
    head: int
    body: int
    tier: str
    note: str
    # Whether the note states a breakpoint worth the prompt space.
    key: bool

    def shots_to_kill(self, effective_hp: int) -> int:
        """Body shots to kill a target with this much effective HP (100/125/150).

        What turns "heavy shield is better" into something a coach can justify: against a
        Vandal light and heavy are both four shots, so the 600 credits bought nothing.
        """
        return math.ceil(effective_hp / self.body) if self.body > 0 else 0


@dataclass(frozen=True, slots=True)
class ArmorOption:
    name: str
    cost: int
    effective_hp: int
    note: str


@dataclass(frozen=True, slots=True)
class BuyType:
    name: str
    shape: str
    credits_from: int | None
    credits_to: int | None


@dataclass(frozen=True, slots=True)
class EconomyBrief:
    """Prices, rewards and thresholds, so buy advice is not invented at generation time."""

    half_start: int
    cap: int
    kill: int
    round_win: int
    loss_ladder: tuple[int, ...]
    spike_plant: int
    survivor_payout: int
    credit_rules: tuple[str, ...]
    armor: tuple[ArmorOption, ...]
    weapons: tuple[Weapon, ...]
    buy_types: tuple[BuyType, ...]
    priorities: tuple[str, ...]
    dropping: tuple[str, ...]
    sides: tuple[str, ...]
    round_context: tuple[str, ...]
    weapon_rules: tuple[str, ...]
    map_weapons: Mapping[str, str]


def parse_economy(data: Mapping[str, Any]) -> EconomyBrief:
    where = "economy"
    credits = _field(data, "credits", where)
    ladder = _field(credits, "loss_ladder", where)
    if not isinstance(ladder, list) or not all(isinstance(v, int) for v in ladder):
        raise KnowledgeError(f"{where}: loss_ladder must be a list of integers")

    weapons: list[Weapon] = []
    for raw in _field(data, "weapons", where):
        name = str(_field(raw, "name", where))
        weapons.append(
            Weapon(
                name=name,
                cost=int(_field(raw, "cost", f"weapon {name}")),
                head=int(_field(raw, "head", f"weapon {name}")),
                body=int(_field(raw, "body", f"weapon {name}")),
                tier=str(_field(raw, "tier", f"weapon {name}")),
                note=str(raw.get("note", "")),
                key=bool(raw.get("key", False)),
            )
        )
    if not weapons:
        raise KnowledgeError(f"{where}: no weapons")

    armor = tuple(
        ArmorOption(
            name=str(_field(raw, "name", "armor")),
            cost=int(_field(raw, "cost", "armor")),
            effective_hp=int(_field(raw, "effective_hp", "armor")),
            note=str(raw.get("note", "")),
        )
        for raw in _field(data, "armor", where)
    )
    buy_types = tuple(
        BuyType(
            name=str(_field(raw, "name", "buy type")),
            shape=str(_field(raw, "shape", "buy type")),
            credits_from=raw.get("from"),
            credits_to=raw.get("to"),
        )
        for raw in _field(data, "buy_types", where)
    )
    return EconomyBrief(
        half_start=int(_field(credits, "half_start", where)),
        cap=int(_field(credits, "cap", where)),
        kill=int(_field(credits, "kill", where)),
        round_win=int(_field(credits, "round_win", where)),
        loss_ladder=tuple(ladder),
        spike_plant=int(_field(credits, "spike_plant", where)),
        survivor_payout=int(_field(credits, "survivor_payout", where)),
        credit_rules=_strings(credits, "rules", where),
        armor=armor,
        weapons=tuple(weapons),
        buy_types=buy_types,
        priorities=_strings(data, "priorities", where),
        dropping=_strings(data, "dropping", where),
        sides=_strings(data, "sides", where),
        round_context=_strings(data, "round_context", where),
        weapon_rules=_strings(data, "weapon_rules", where),
        map_weapons={str(k): str(v) for k, v in dict(_field(data, "map_weapons", where)).items()},
    )


def load_economy() -> EconomyBrief:
    return parse_economy(_read("economy.json"))


@dataclass(frozen=True, slots=True)
class Practice:
    """What to do about a category: one rule for ranked, one drill for outside it."""

    rule: str
    drill: str


@dataclass(frozen=True, slots=True)
class Drills:
    by_category: Mapping[str, Practice]

    def for_category(self, category: str) -> Practice | None:
        return self.by_category.get(category) or self.by_category.get("other")


def parse_drills(data: Mapping[str, Any]) -> Drills:
    raw = _field(data, "categories", "drills")
    if not isinstance(raw, Mapping):
        raise KnowledgeError("drills: categories must be an object")
    out: dict[str, Practice] = {}
    for category, practice in raw.items():
        where = f"drill {category}"
        out[str(category)] = Practice(
            rule=str(_field(practice, "rule", where)),
            drill=str(_field(practice, "drill", where)),
        )
    return Drills(out)


@cache
def load_drills() -> Drills:
    return parse_drills(_read("drills.json"))


@dataclass(frozen=True, slots=True)
class CoachingKnowledge:
    checklist: Checklist
    agents: Mapping[str, AgentBrief]
    maps: Mapping[str, MapBrief]
    economy: EconomyBrief


@cache
def load_knowledge() -> CoachingKnowledge:
    return CoachingKnowledge(load_checklist(), load_agents(), load_maps(), load_economy())


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
    # A planned buy window judges the purchase and nothing else: the player is standing
    # in spawn reading a menu, so positioning and crosshair advice there is noise.
    "buy": frozenset({"economy"}),
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


ECO_NOTE: dict[str, str] = {
    "strong": "functions on a pistol round: the key ability is free or very cheap",
    "crippled": "needs most of the kit to function, so an eco round costs more than the gun",
}


def render_agent_brief(agent: AgentBrief) -> str:
    eco = ECO_NOTE.get(agent.eco)
    head = f"Agent brief: {agent.name} ({agent.role}). {agent.job_in_round}"
    if eco:
        head += f" On a thin buy, {agent.name} {eco}."
    lines = [head]
    lines += _bullets(
        "Abilities",
        [
            f"{a.key} {a.name} ({a.cost}): {a.purpose} Use it: {a.when}."
            + (f" Worth it? {a.verdict}." if a.verdict else "")
            for a in agent.abilities
        ],
    )
    lines += _bullets("Good play looks like", agent.good_play_looks_like)
    lines += _bullets("Common mistakes to look for", agent.common_mistakes)
    lines += _bullets("HUD ability checks", agent.ability_checks)
    return "\n".join(lines)


def render_economy_brief(economy: EconomyBrief, map_id: str | None = None) -> str:
    """The prices, rewards and thresholds a buy decision turns on.

    Sent only on buy windows. Everywhere else it is 3,000 characters of prompt spent on a
    decision the player is not making.
    """
    guns = ", ".join(f"{w.name} {w.cost}" for w in economy.weapons if w.cost >= 300)
    armour = ", ".join(f"{a.name} {a.cost} ({a.effective_hp} effective HP)" for a in economy.armor)
    ladder = ", ".join(f"{c:,}" for c in economy.loss_ladder)
    lines = [
        "Economy brief (credits are exact; buy-type thresholds are convention, give or take 300):",
        f"  Prices: {guns}.",
        f"  Armor: {armour}.",
        f"  Rewards: kill {economy.kill}, round win {economy.round_win:,}, "
        f"loss {ladder} on a streak, spike plant {economy.spike_plant}, "
        f"cap {economy.cap:,}, {economy.half_start} at the start of a half.",
    ]
    lines += [f"  - {rule}" for rule in economy.credit_rules]
    lines.append("  Buy types:")
    for buy in economy.buy_types:
        span = ""
        if buy.credits_from and buy.credits_to:
            span = f" ({buy.credits_from:,}-{buy.credits_to:,})"
        elif buy.credits_from:
            span = f" ({buy.credits_from:,}+)"
        elif buy.credits_to:
            span = f" (under {buy.credits_to:,})"
        lines.append(f"  - {buy.name}{span}: {buy.shape}")
    lines.append("  Priorities:")
    lines += [f"  - {rule}" for rule in economy.priorities]
    lines.append("  Dropping:")
    lines += [f"  - {rule}" for rule in economy.dropping]
    lines.append("  Sides:")
    lines += [f"  - {rule}" for rule in economy.sides]
    lines.append("  Round context:")
    lines += [f"  - {rule}" for rule in economy.round_context]
    lines.append("  Weapon rules:")
    lines += [f"  - {rule}" for rule in economy.weapon_rules]
    # Only the map in play: the other six are prompt spent on a game nobody is playing.
    for_map = economy.map_weapons.get(_slug(map_id or ""))
    if for_map:
        lines.append(f"  On this map: {for_map}")
    notable = [w for w in economy.weapons if w.note and w.key]
    lines.append("  Weapon notes:")
    lines += [f"  - {w.name}: {w.note}" for w in notable]
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
