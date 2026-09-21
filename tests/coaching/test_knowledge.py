import json
from pathlib import Path

import pytest

from round_review.coaching.knowledge import (
    AgentBrief,
    Checklist,
    MapBrief,
    find_agent,
    find_map,
    load_agents,
    load_checklist,
    load_maps,
    parse_agents,
    parse_checklist,
    parse_maps,
    render_agent_brief,
    render_checklist,
    render_map_brief,
    render_rank_focus,
)
from round_review.errors import KnowledgeError


def test_bundled_checklist_loads_and_is_well_formed() -> None:
    checklist = load_checklist()
    assert isinstance(checklist, Checklist)
    ids = [c.id for cat in checklist.categories for c in cat.checks]
    assert len(ids) == len(set(ids)), "duplicate check ids"
    assert all(c.id.startswith(f"{cat.id}.") for cat in checklist.categories for c in cat.checks)
    assert checklist.check_ids() == frozenset(ids)
    assert set(checklist.rank_expectations) >= {
        "iron_bronze",
        "silver_gold",
        "platinum_diamond",
        "ascendant_plus",
    }


def test_bundled_agents_and_maps_load() -> None:
    agents = load_agents()
    maps = load_maps()
    assert all(isinstance(a, AgentBrief) for a in agents.values())
    assert all(isinstance(m, MapBrief) for m in maps.values())
    assert all(
        a.role in {"duelist", "initiator", "controller", "sentinel"} for a in agents.values()
    )
    assert all(key == a.id for key, a in agents.items())
    assert all(key == m.id for key, m in maps.items())


def test_find_agent_is_lenient_on_name() -> None:
    agents = parse_agents(
        {
            "agents": [
                {
                    "id": "kayo",
                    "name": "KAY/O",
                    "role": "initiator",
                    "abilities": [],
                    "job_in_round": "j",
                    "good_play_looks_like": [],
                    "common_mistakes": [],
                    "ability_checks": [],
                    "tips": [],
                }
            ]
        }
    )
    assert find_agent(agents, "KAY/O") is not None
    assert find_agent(agents, " kay/o ") is not None
    assert find_agent(agents, "kayo") is not None
    assert find_agent(agents, "Jett") is None
    assert find_agent(agents, None) is None


def test_find_map_is_lenient_on_name() -> None:
    maps = parse_maps(
        {
            "maps": [
                {
                    "id": "ascent",
                    "name": "Ascent",
                    "in_competitive_rotation": True,
                    "sites": ["A", "B"],
                    "layout_summary": "s",
                    "key_callouts": [],
                    "attack_defaults": [],
                    "defense_setups": [],
                    "power_positions": [],
                    "common_mistakes": [],
                    "utility_notes": [],
                }
            ]
        }
    )
    assert find_map(maps, "ASCENT") is not None
    assert find_map(maps, "Bind") is None


def test_parse_checklist_rejects_duplicate_and_misprefixed_ids() -> None:
    base = {
        "categories": [
            {
                "id": "a",
                "name": "A",
                "principle": "p",
                "checks": [
                    {
                        "id": "a.x",
                        "check": "c",
                        "common_mistake": "m",
                        "fix": "f",
                        "visible_in_frames": True,
                    },
                    {
                        "id": "a.x",
                        "check": "c",
                        "common_mistake": "m",
                        "fix": "f",
                        "visible_in_frames": True,
                    },
                ],
            }
        ],
        "rank_expectations": {},
    }
    with pytest.raises(KnowledgeError, match="duplicate"):
        parse_checklist(base)
    bad = json.loads(json.dumps(base))
    bad["categories"][0]["checks"][1]["id"] = "b.y"
    with pytest.raises(KnowledgeError, match="prefix"):
        parse_checklist(bad)


def test_parse_missing_field_is_knowledge_error() -> None:
    with pytest.raises(KnowledgeError, match="principle"):
        parse_checklist(
            {"categories": [{"id": "a", "name": "A", "checks": []}], "rank_expectations": {}}
        )


def test_render_checklist_is_compact_and_lists_ids() -> None:
    checklist = load_checklist()
    text = render_checklist(checklist)
    first = checklist.categories[0]
    assert first.name in text
    assert first.checks[0].id in text
    assert first.checks[0].check in text
    # one line per check plus one per category header; no fixes/mistakes in the compact form
    assert (
        text.count("\n")
        < sum(len(c.checks) for c in checklist.categories) * 2 + len(checklist.categories) * 2
    )


def test_render_checklist_can_drop_checks_not_visible_in_frames() -> None:
    checklist = parse_checklist(
        {
            "categories": [
                {
                    "id": "a",
                    "name": "A",
                    "principle": "p",
                    "checks": [
                        {
                            "id": "a.seen",
                            "check": "seen",
                            "common_mistake": "m",
                            "fix": "f",
                            "visible_in_frames": True,
                        },
                        {
                            "id": "a.hidden",
                            "check": "hidden",
                            "common_mistake": "m",
                            "fix": "f",
                            "visible_in_frames": False,
                        },
                    ],
                }
            ],
            "rank_expectations": {},
        }
    )
    text = render_checklist(checklist, visible_only=True)
    assert "a.seen" in text and "a.hidden" not in text


def test_render_agent_and_map_briefs() -> None:
    agent = AgentBrief(
        "jett",
        "Jett",
        "duelist",
        (("E", "Tailwind", "dash to reposition"),),
        "entry",
        ("dashes after kill",),
        ("dies with dash up",),
        ("dash icon lit at death = wasted",),
        ("tip",),
    )
    text = render_agent_brief(agent)
    assert "Jett (duelist)" in text and "Tailwind" in text and "dies with dash up" in text
    m = MapBrief(
        "bind",
        "Bind",
        True,
        ("A", "B"),
        "teleporters",
        ("Hookah: B entry",),
        ("default A",),
        ("2-1-2",),
        ("Elbow",),
        ("push Hookah blind",),
        ("smoke Showers",),
    )
    text = render_map_brief(m)
    assert "Bind" in text and "Hookah" in text and "push Hookah blind" in text


def test_render_rank_focus() -> None:
    checklist = load_checklist()
    assert render_rank_focus(checklist, "Gold 2") == checklist.rank_expectations["silver_gold"]
    assert (
        render_rank_focus(checklist, "Immortal 1") == checklist.rank_expectations["ascendant_plus"]
    )
    assert render_rank_focus(checklist, "Iron") == checklist.rank_expectations["iron_bronze"]
    assert (
        render_rank_focus(checklist, "diamond") == checklist.rank_expectations["platinum_diamond"]
    )
    assert render_rank_focus(checklist, None) is None
    assert render_rank_focus(checklist, "banana") is None


def test_data_files_are_valid_json(tmp_path: Path) -> None:
    from importlib.resources import files

    root = files("round_review.coaching.knowledge")
    for name in ("checklist.json", "agents.json", "maps.json"):
        json.loads(root.joinpath(name).read_text(encoding="utf-8"))
