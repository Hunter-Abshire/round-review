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
    relevant_categories,
    render_agent_brief,
    render_checklist,
    render_map_brief,
    render_rank_focus,
)
from round_review.coaching.situation import Situation
from round_review.video.frames import FrameSample
from round_review.video.windows import Window
from round_review.vision.state import HudState

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
MAX_STRENGTHS_PER_WINDOW = 2
MAX_FOCUS_SHAPES = 4

# Shapes the model may draw over the frame, in fractions of its width and height.
FOCUS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": MAX_FOCUS_SHAPES,
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["box", "point", "arrow"]},
            "label": {"type": "string"},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "w": {"type": "number", "minimum": 0, "maximum": 1},
            "h": {"type": "number", "minimum": 0, "maximum": 1},
            "x2": {"type": "number", "minimum": 0, "maximum": 1},
            "y2": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["kind", "label", "x", "y"],
    },
}

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

7. Judge decisions immediately before or during a live encounter. BUY PHASE, barriers,
menus, spectating, and safe travel alone are not evidence of a combat mistake. Do not
criticize crosshair placement while holding a knife or an ability. Knife-out travel
is only a weapon-readiness issue if a specific visible threat makes it unsafe.
8. In the observation, explain the practical risk to this fight or objective, tied to
a visible enemy, exposed angle, teammate, clock, or ability opportunity. If you cannot
establish why it matters here, omit it. Never turn a checklist into a quota of mistakes
or repeat several versions of the same aim criticism at one moment.
9. Prioritize avoidable deaths, exposure to multiple angles, support and trade opportunities,
purposeful utility, and objective timing when the frames support them. Do not invent
sound cues, comms, enemy locations, or actions between sampled frames. For a new or
unranked player explain the reason and the next action in plain language.

10. Moving through the map with no enemy on screen and no contact in the timeline is
rotating or repositioning, not holding an angle. Do not criticise the position, the angles
covered, or the distance to teammates in that case: a player crossing the map alone is
meant to be alone, and trade distance only matters where contact is happening or plainly
imminent. Judge positioning where the player chooses to stop, hold, or enter.
11. Do not tell the player to be somewhere you cannot see, or to be with teammates whose
positions are not visible. If your reason rests on where you assume enemies or teammates
are, either say that in the assumptions and lower your confidence, or leave it out.
12. Also report up to two strengths: things the player did right that are worth keeping.
A strength must name the specific behaviour and why it worked, in the same evidence-bound
way as a finding. Generic praise is worse than none, so never write "good job", "nice
aim" or any other vague or filler compliment, and report no strengths at all rather than
inventing one. Being alive, winning the fight, or the enemy playing badly are not strengths.

13. A player who is stopped is usually holding an angle on purpose, and a round that is
already won or already lost is not coached. When the player's side has a decisive numbers
advantage, or the round clock has nearly run out and nothing is contested, waiting is the
correct play: do not ask for a push, a rotate, or a better angle. Criticise a held angle
only when you can name what is wrong with that angle, not merely that the player stood in
one place.

14. You may add a focus list to any finding or strength, marking what to look at on the
frame at that timestamp. Use x and y as fractions of the frame's width and height, measured
from the top left, so 0.5, 0.5 is the centre. A box needs w and h, an arrow needs an end
point at x2, y2, a point needs nothing more. Give every shape a short label. Only mark
something you can actually see in the frame, and leave the focus list out entirely rather
than guessing at coordinates.

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
                    "focus": FOCUS_SCHEMA,
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
        },
        "strengths": {
            "type": "array",
            "maxItems": MAX_STRENGTHS_PER_WINDOW,
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_s": {"type": "number"},
                    "check_id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "observation": {"type": "string"},
                    "visible_evidence": {"type": "string"},
                    "why_it_worked": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "focus": FOCUS_SCHEMA,
                },
                "required": [
                    "timestamp_s",
                    "check_id",
                    "category",
                    "observation",
                    "visible_evidence",
                    "why_it_worked",
                    "confidence",
                ],
            },
        },
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
            "enum": ["pre_round", "early", "mid", "post_plant", "retake", "spectating", "unknown"],
        },
        "weapon": {"type": "string"},
        "abilities_available": {"type": "array", "items": {"type": "string"}},
        "credits": {"type": ["integer", "null"]},
        "teammates_alive": {"type": ["integer", "null"]},
        "enemies_alive": {"type": ["integer", "null"]},
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
        "enemies_alive",
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
    "Classify phase from visible HUD evidence. pre_round requires visible BUY PHASE text; "
    "no visible enemies, green crates, walls, or preparing a move do not establish pre_round. "
    "A running round timer and recent kills without BUY PHASE indicate live play. A death "
    "combat report plus SWITCH PLAYER indicates spectating; "
    "do not attribute that POV to the player. "
    "Use pre_round or spectating only if all frames remain in that state; if "
    "live play starts, use its phase and mark the transition in the timeline. Name a weapon "
    "only from what is equipped; a knife is not a firearm. An area label or player name is "
    "not the map or agent name. Never fill unreadable HUD fields with invented values. "
    "Respond with JSON only, matching the schema you are given."
)


def _clock_range(seconds: float) -> str:
    """m:ss, for talking to the player about a position in the recording."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _captions(samples: Sequence[FrameSample]) -> str:
    return "\n".join(f"Frame {i + 1}: t={s.timestamp_s:.1f}s" for i, s in enumerate(samples))


def _window_line(window: Window, samples: Sequence[FrameSample]) -> str:
    return (
        f"This window covers t={window.start_s:.1f}s to t={window.end_s:.1f}s of the recording. "
        f"You are given {len(samples)} frames, attached in this order:\n{_captions(samples)}"
    )


def build_system_prompt(knowledge: CoachingKnowledge, phase: str | None) -> str:
    """Persona, rules and the checklist. When the round phase is known only the relevant
    categories are included, which keeps the prompt small next to the images."""
    checklist = render_checklist(
        knowledge.checklist, visible_only=True, categories=relevant_categories(phase)
    )
    return f"{PERSONA}\n\n{RULES}\n\nReview checklist (cite ids in check_id):\n{checklist}"


def build_situation_prompt(
    window: Window, samples: Sequence[FrameSample], context: PlayerContext
) -> str:
    known = context.describe()
    known_line = f"What the player told us: {known}\n\n" if known else ""
    # A told agent is not a question. Left open, a misread portrait becomes the whole
    # coach pass reasoning about somebody else's kit.
    if context.agent:
        known_line += (
            f"The agent is {context.agent}. Report that in the agent field and do not "
            "name a different one.\n\n"
        )
    return (
        f"{known_line}{_window_line(window, samples)}\n\n"
        "Describe the situation: which agent the player is using (portrait and ability icons), "
        "which map (minimap and scenery), attack or defense, the phase of the round, the weapon, "
        "which abilities are still available (lit icons), credits, how many teammates and how "
        "many enemies are still alive (the round counters beside the scoreboard), enemies "
        "visible, and a short timeline of what the player does frame by frame. Finish with a "
        "two-sentence summary."
    )


def build_coach_prompt(
    window: Window,
    samples: Sequence[FrameSample],
    context: PlayerContext,
    situation: Situation | None,
    knowledge: CoachingKnowledge,
    state: HudState | None = None,
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
        kit = ", ".join(f"{name} ({key})" for key, name, _purpose in agent.abilities)
        sections.append(
            f"{agent.name} has exactly these abilities: {kit}. Any other ability belongs to "
            "an agent the player is not using, so never suggest one."
        )
    game_map = find_map(knowledge.maps, context.map)
    if game_map:
        sections.append(render_map_brief(game_map))
    if situation:
        sections.append(situation.describe())
    # After the situation read, so a measured value overrides whatever the model said.
    measured = state.describe() if state else ""
    if measured:
        sections.append(
            f"Measured from the HUD (this is read from the pixels, not inferred, and "
            f"overrides anything above that disagrees): {measured}"
        )
    sections.append(_window_line(window, samples))
    sections.append(
        "Identify the most consequential supported decision just before or during combat. "
        "Use the checklist to explain that decision, not to enumerate cosmetic imperfections. "
        "Report only meaningful mistakes, each as a "
        "finding with the checklist id in check_id, the timestamp (within this window), the "
        "category, what you observed, the visible evidence with the frame timestamp it comes "
        "from, what information the player had at that moment, what only became known later "
        "(empty string if nothing), the assumptions you are making, one suggested alternative "
        f"specific to this agent and map, and your confidence from 0 to 1. At most "
        f"{MAX_FINDINGS_PER_WINDOW} findings; pick the ones that would change the round. "
        "Return an empty findings list when this is only preparation, safe travel, or the "
        "evidence is insufficient. Copy the relevant frame timestamp exactly.\n\n"
        f"Then, separately, report up to {MAX_STRENGTHS_PER_WINDOW} strengths: deliberate "
        "good decisions in these frames worth reinforcing, each with the checklist id it "
        "satisfies, what you saw, and why it worked. Leave the strengths list empty unless "
        "something specific deserves it; generic or filler praise is not wanted."
    )
    return "\n\n".join(sections)
