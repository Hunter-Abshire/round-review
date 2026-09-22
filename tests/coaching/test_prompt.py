import json
from pathlib import Path

from round_review.coaching.context import PlayerContext
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.prompt import (
    FINDING_SCHEMA,
    SITUATION_SCHEMA,
    build_coach_prompt,
    build_situation_prompt,
    build_system_prompt,
)
from round_review.coaching.situation import Situation
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(index=1, start_s=100.0, end_s=112.0, source="evenly_spaced")
SAMPLES = [FrameSample(1, 100.0 + i, Path(f"/f/{i}.jpg")) for i in range(3)]


def test_schemas_are_json_serialisable_and_require_anti_hindsight_fields() -> None:
    json.dumps(FINDING_SCHEMA)
    json.dumps(SITUATION_SCHEMA)
    finding = FINDING_SCHEMA["properties"]["findings"]["items"]
    for key in (
        "timestamp_s",
        "check_id",
        "category",
        "observation",
        "visible_evidence",
        "information_available_to_player",
        "information_revealed_later",
        "assumption_flags",
        "suggested_alternative",
        "confidence",
    ):
        assert key in finding["required"], key
    for key in ("agent", "map", "side", "phase", "abilities_available", "timeline", "summary"):
        assert key in SITUATION_SCHEMA["required"], key


def test_system_prompt_contains_persona_rules_and_checklist() -> None:
    knowledge = load_knowledge()
    text = build_system_prompt(knowledge, phase=None)
    lower = text.lower()
    assert "coach" in lower
    assert "later" in lower and "earlier" in lower
    assert "json" in lower
    first_check = knowledge.checklist.categories[0].checks[0]
    assert f"[{first_check.id}]" in text


def test_situation_prompt_captions_frames_and_asks_for_hud() -> None:
    text = build_situation_prompt(WINDOW, SAMPLES, PlayerContext(agent="Jett"))
    assert text.index("t=100.0s") < text.index("t=101.0s") < text.index("t=102.0s")
    lower = text.lower()
    assert "minimap" in lower and "abilit" in lower and "credits" in lower
    assert "Agent: Jett." in text


def test_coach_prompt_includes_briefs_situation_and_rank_focus() -> None:
    knowledge = load_knowledge()
    ctx = PlayerContext(rank="Gold 2", agent="Jett", map="Ascent", side="attack", focus="entries")
    situation = Situation(
        agent="Jett",
        map="Ascent",
        side="attack",
        phase="early",
        weapon="Vandal",
        abilities_available=("Tailwind",),
        credits=3900,
        teammates_alive=4,
        enemies_alive=4,
        enemies_visible=0,
        timeline=((100.0, "walking A main"),),
        summary="Entering A main with dash up.",
    )
    text = build_coach_prompt(WINDOW, SAMPLES, ctx, situation, knowledge)
    assert "Agent brief: Jett (duelist)" in text
    assert "Map brief: Ascent" in text
    assert knowledge.checklist.rank_expectations["silver_gold"] in text
    assert "Entering A main with dash up." in text
    assert "walking A main" in text
    assert "Focus: entries." in text
    assert "3 frames" in text
    assert "check_id" in text


def test_coach_prompt_without_context_or_situation_still_works() -> None:
    knowledge = load_knowledge()
    text = build_coach_prompt(WINDOW, SAMPLES, PlayerContext(), None, knowledge)
    assert "Agent brief" not in text and "Map brief" not in text
    assert "t=100.0s" in text


def test_system_prompt_drops_irrelevant_categories_for_known_phase() -> None:
    knowledge = load_knowledge()
    text = build_system_prompt(knowledge, phase="early")
    assert "[crosshair." in text and "[postplant." not in text and "[retake." not in text
    full = build_system_prompt(knowledge, phase=None)
    assert "[postplant." in full


def test_the_schema_allows_strengths_but_does_not_demand_them() -> None:
    assert "strengths" in FINDING_SCHEMA["properties"]
    # not required: a window with nothing worth praising must be able to say so
    assert "strengths" not in FINDING_SCHEMA["required"]
    item = FINDING_SCHEMA["properties"]["strengths"]["items"]
    for key in (
        "timestamp_s",
        "check_id",
        "category",
        "observation",
        "visible_evidence",
        "why_it_worked",
        "confidence",
    ):
        assert key in item["required"], key
    assert FINDING_SCHEMA["properties"]["strengths"]["maxItems"] == 2


def test_the_coach_prompt_asks_for_specific_praise_not_filler() -> None:
    knowledge = load_knowledge()
    text = build_coach_prompt(WINDOW, SAMPLES, PlayerContext(), None, knowledge)
    lower = text.lower()
    assert "strength" in lower
    assert "vague" in lower or "filler" in lower or "generic" in lower


def test_the_prompt_says_that_crossing_the_map_is_not_holding_an_angle() -> None:
    text = build_system_prompt(load_knowledge(), phase=None).lower()
    assert "rotating" in text or "crossing" in text
    assert "trade" in text
