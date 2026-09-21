"""Prompt text and the JSON schemas for both passes.

Pass 1 (situation): describe what is on screen. Pass 2 (coach): judge the player against the
checklist, with agent and map briefs and the situation read as context.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from round_review.coaching.context import PlayerContext
from round_review.coaching.knowledge import (
    CoachingKnowledge,
    find_agent,
    find_map,
    render_agent_brief,
    render_checklist,
    render_map_brief,
    render_rank_focus,
)
from round_review.coaching.situation import Situation
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

CATEGORIES: tuple[str, ...] = (
    "crosshair",
    "movement",
    "peeking",
    "positioning",
    "utility",
    "trading",
    "economy",
    "info",
    "timing",
    "postplant",
    "retake",
    "mental",
    "other",
)

MAX_FINDINGS_PER_WINDOW = 4

PERSONA = (
    "You are an experienced Valorant coach who has reviewed thousands of ranked VODs from Iron "
    "to Immortal. You are specific, blunt and evidence-driven: you name the exact habit, the "
    "exact moment, and the exact fix. You never give generic advice like 'aim better' or "
    "'communicate more'."
)

RULES = """Rules you must follow:
1. The player could only see what is on screen at each frame. Never judge a decision made \
in an earlier frame using anything that only becomes visible in a later frame.
2. Separate what you can actually see from what you are assuming. Enemy positions, ability \
cooldowns and team economy are assumptions unless the HUD or minimap shows them.
3. A reasonable decision that ended badly is not a mistake. Only report decisions that were \
poor given the information available at that moment.
4. Every finding must cite one checklist item by its id in check_id. If nothing on the \
checklist applies, do not invent a finding.
5. Prefer fewer, well-evidenced findings over many weak ones. Return no findings if the \
frames do not support any.
6. Each finding needs one concrete, practical alternative the player can try next match, \
phrased for their rank.

Respond with JSON only, matching the schema you are given. No prose outside the JSON."""

RETRY_NUDGE = (
    "\n\nYour previous reply was not valid JSON matching the schema. Reply again with ONLY the "
    'JSON object, starting with {"findings": [ and nothing else.'
)

FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": MAX_FINDINGS_PER_WINDOW,
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_s": {"type": "number"},
                    "check_id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "observation": {"type": "string"},
                    "visible_evidence": {"type": "string"},
                    "information_available_to_player": {"type": "string"},
                    "information_revealed_later": {"type": "string"},
                    "assumption_flags": {"type": "array", "items": {"type": "string"}},
                    "suggested_alternative": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
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
                ],
            },
        }
    },
    "required": ["findings"],
}

SITUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "map": {"type": "string"},
        "side": {"type": "string", "enum": ["attack", "defense", "unknown"]},
        "phase": {
            "type": "string",
            "enum": ["pre_round", "early", "mid", "post_plant", "retake", "unknown"],
        },
        "weapon": {"type": "string"},
        "abilities_available": {"type": "array", "items": {"type": "string"}},
        "credits": {"type": ["integer", "null"]},
        "teammates_alive": {"type": ["integer", "null"]},
        "enemies_visible": {"type": "integer"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"t": {"type": "number"}, "event": {"type": "string"}},
                "required": ["t", "event"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": [
        "agent",
        "map",
        "side",
        "phase",
        "weapon",
        "abilities_available",
        "credits",
        "teammates_alive",
        "enemies_visible",
        "timeline",
        "summary",
    ],
}

SITUATION_SYSTEM_PROMPT = (
    "You are an analyst describing frames from a Valorant recording. Report only what is "
    "visible. Use the HUD: the agent portrait and ability icons bottom-centre, the minimap "
    "top-left, credits and weapon bottom-right, the round timer and score top-centre, "
    "teammate portraits at the top. Write 'unknown' when something is not visible. "
    "Respond with JSON only, matching the schema you are given."
)


def _captions(samples: Sequence[FrameSample]) -> str:
    return "\n".join(f"Frame {i + 1}: t={s.timestamp_s:.1f}s" for i, s in enumerate(samples))


def _window_line(window: Window, samples: Sequence[FrameSample]) -> str:
    return (
        f"This window covers t={window.start_s:.1f}s to t={window.end_s:.1f}s of the recording. "
        f"You are given {len(samples)} frames, attached in this order:\n{_captions(samples)}"
    )


def build_system_prompt(knowledge: CoachingKnowledge) -> str:
    return (
        f"{PERSONA}\n\n{RULES}\n\nReview checklist (cite ids in check_id):\n"
        f"{render_checklist(knowledge.checklist, visible_only=True)}"
    )


def build_situation_prompt(
    window: Window, samples: Sequence[FrameSample], context: PlayerContext
) -> str:
    known = context.describe()
    known_line = f"What the player told us: {known}\n\n" if known else ""
    return (
        f"{known_line}{_window_line(window, samples)}\n\n"
        "Describe the situation: which agent the player is using (portrait and ability icons), "
        "which map (minimap and scenery), attack or defense, the phase of the round, the weapon, "
        "which abilities are still available (lit icons), credits, teammates alive, enemies "
        "visible, and a short timeline of what the player does frame by frame. Finish with a "
        "two-sentence summary."
    )


def build_coach_prompt(
    window: Window,
    samples: Sequence[FrameSample],
    context: PlayerContext,
    situation: Situation | None,
    knowledge: CoachingKnowledge,
) -> str:
    sections: list[str] = []
    described = context.describe()
    if described:
        sections.append(f"Player context: {described}")
    focus = render_rank_focus(knowledge.checklist, context.rank)
    if focus:
        sections.append(f"Coaching priorities at this rank: {focus}")
    agent = find_agent(knowledge.agents, context.agent)
    if agent:
        sections.append(render_agent_brief(agent))
    game_map = find_map(knowledge.maps, context.map)
    if game_map:
        sections.append(render_map_brief(game_map))
    if situation:
        sections.append(situation.describe())
    sections.append(_window_line(window, samples))
    sections.append(
        "Go through the checklist. For every item you can judge from these frames and the "
        "situation read, decide whether the player passes. Report only failures, each as a "
        "finding with the checklist id in check_id, the timestamp (within this window), the "
        "category, what you observed, the visible evidence with the frame timestamp it comes "
        "from, what information the player had at that moment, what only became known later "
        "(empty string if nothing), the assumptions you are making, one suggested alternative "
        f"specific to this agent and map, and your confidence from 0 to 1. At most "
        f"{MAX_FINDINGS_PER_WINDOW} findings; pick the ones that would change the round."
    )
    return "\n\n".join(sections)
