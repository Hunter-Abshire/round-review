"""Asking the coach about one specific stretch of the recording.

The per-match report answers the questions a coach would raise unprompted. This answers the
one the player actually has: "what should I have done there", "how could I have used utility
here". Same evidence discipline as a finding, and the same refusal to use hindsight.

It offers several alternatives rather than one instruction on purpose: the autonomy-support
research treats a choice of solutions as one of the features that make corrective feedback
land, and it is the feature automated feedback almost always drops.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
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
from round_review.coaching.parse import extract_json
from round_review.coaching.prompt import PERSONA
from round_review.coaching.situation import Situation
from round_review.errors import ParseError
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

MAX_ALTERNATIVES = 3
# A question about "this moment" is about a moment; a minute of footage is a different ask.
DEFAULT_MAX_SPAN_S = 60.0
# What a bare click means: a few seconds either side of the playhead.
CLICK_SPAN_S = 8.0
# Above this, an answer that leans on later information is pulled back to here.
HINDSIGHT_CONFIDENCE_CEILING = 0.5
HINDSIGHT_TRIGGER = 0.7
PLACEHOLDER: frozenset[str] = frozenset({"", "none", "n/a", "na", "nothing", "-"})

REQUIRED_FIELDS: tuple[str, ...] = (
    "answerable",
    "answer",
    "what_you_could_see",
    "what_you_could_not_know",
    "assumptions",
    "alternatives",
    "confidence",
)


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    start_s: float
    end_s: float
    question: str


@dataclass(frozen=True, slots=True)
class Alternative:
    action: str
    why: str


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    start_s: float
    end_s: float
    answerable: bool
    answer: str
    what_you_could_see: str
    what_you_could_not_know: str
    assumptions: tuple[str, ...]
    alternatives: tuple[Alternative, ...]
    confidence: float
    warnings: tuple[str, ...] = ()


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "answer": {"type": "string"},
        "what_you_could_see": {"type": "string"},
        "what_you_could_not_know": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "alternatives": {
            "type": "array",
            "maxItems": MAX_ALTERNATIVES,
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "why": {"type": "string"}},
                "required": ["action", "why"],
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": list(REQUIRED_FIELDS),
}

QUESTION_RULES = """Rules you must follow:
1. Answer the question that was asked, in the first sentence, in plain language.
2. Use only what is visible in these frames. If they do not show enough to answer, set
answerable to false and say so plainly rather than guessing. Saying you cannot tell is a
useful answer; inventing one is not.
3. Never judge an earlier decision using anything that only became visible later in these
frames. Put anything of that kind in what_you_could_not_know, and do not hold it against
the player.
4. Offer up to three alternatives the player could have chosen, each with one line on why it
would have been better. Give options rather than a single instruction.
5. Name every assumption you make. Enemy positions, ability cooldowns, teammate intentions
and comms are assumptions unless the HUD or minimap shows them.
6. Do not comment on the player as a person, and do not praise or criticise the outcome. The
question is about the decision.

Respond with JSON only, matching the schema you are given. No prose outside the JSON."""


def clamp_span(
    start_s: float, end_s: float, duration_s: float, max_span_s: float = DEFAULT_MAX_SPAN_S
) -> tuple[float, float]:
    """Keep a requested range inside the recording and inside something answerable.

    A zero-length or backwards range is treated as a click at `start_s`, which becomes a few
    seconds either side of it.
    """
    if duration_s <= 0:
        raise ValueError("duration_s must be > 0")
    if end_s <= start_s:
        # A click means "around here": keep the full span by shifting it inside the
        # recording rather than shrinking it at the edges.
        half = CLICK_SPAN_S / 2
        start_s = min(max(0.0, start_s - half), max(0.0, duration_s - CLICK_SPAN_S))
        end_s = start_s + CLICK_SPAN_S
    start = min(max(0.0, start_s), duration_s)
    end = min(max(start, end_s), duration_s)
    if end - start > max_span_s:
        end = start + max_span_s
    return start, min(end, duration_s)


def build_question_system_prompt(knowledge: CoachingKnowledge, phase: str | None) -> str:
    checklist = render_checklist(
        knowledge.checklist, visible_only=True, categories=relevant_categories(phase)
    )
    return (
        f"{PERSONA}\n\nA player has asked you about one specific moment in their own "
        f"recording.\n\n{QUESTION_RULES}\n\nFor reference, the things you look for:\n{checklist}"
    )


def build_question_prompt(
    window: Window,
    samples: Sequence[FrameSample],
    spec: QuestionSpec,
    context: PlayerContext,
    situation: Situation | None,
    knowledge: CoachingKnowledge,
) -> str:
    from round_review.coaching.prompt import _captions, _clock_range

    sections: list[str] = [f'The player asks: "{spec.question.strip()}"']
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
    sections.append(
        f"The moment they are asking about runs from {_clock_range(window.start_s)} to "
        f"{_clock_range(window.end_s)} of the recording. You are given {len(samples)} frames, "
        f"attached in this order:\n{_captions(samples)}"
    )
    sections.append(
        "Answer their question from these frames. Give the answer first, then what was "
        "visible, then what only became clear later, then your assumptions, then up to "
        f"{MAX_ALTERNATIVES} alternatives they could have chosen instead, each with why. If "
        "the frames do not support an answer, set answerable to false and say what you would "
        "need to see."
    )
    return "\n\n".join(sections)


def _is_placeholder(text: str) -> bool:
    return text.strip().rstrip(".").lower() in PLACEHOLDER


def parse_answer(text: str, spec: QuestionSpec) -> Answer:
    """Parse the reply. A missing required field is an error; a bad alternative is a warning."""
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise ParseError(f"answer is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("answer is not an object")
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        raise ParseError(f"answer missing required field(s): {', '.join(missing)}")

    warnings: list[str] = []
    try:
        confidence = min(1.0, max(0.0, float(payload["confidence"])))
        assumptions = tuple(str(a) for a in payload["assumptions"])
    except (TypeError, ValueError) as exc:
        raise ParseError(f"answer has a malformed numeric or list field: {exc}") from exc

    alternatives: list[Alternative] = []
    raw_alternatives = payload["alternatives"]
    if not isinstance(raw_alternatives, list):
        warnings.append("alternatives was not a list; ignored")
        raw_alternatives = []
    for raw in raw_alternatives:
        if not isinstance(raw, dict) or "action" not in raw or "why" not in raw:
            warnings.append("dropped an alternative without both an action and a why")
            continue
        alternatives.append(Alternative(str(raw["action"]), str(raw["why"])))
    if len(alternatives) > MAX_ALTERNATIVES:
        warnings.append(f"more than {MAX_ALTERNATIVES} alternatives offered; truncated")
        alternatives = alternatives[:MAX_ALTERNATIVES]

    later = str(payload["what_you_could_not_know"])
    if not _is_placeholder(later) and confidence > HINDSIGHT_TRIGGER:
        warnings.append(
            "this answer leans on information that only became clear later; confidence "
            f"pulled back to {HINDSIGHT_CONFIDENCE_CEILING}"
        )
        confidence = HINDSIGHT_CONFIDENCE_CEILING

    return Answer(
        question=spec.question.strip(),
        start_s=spec.start_s,
        end_s=spec.end_s,
        answerable=bool(payload["answerable"]),
        answer=str(payload["answer"]),
        what_you_could_see=str(payload["what_you_could_see"]),
        what_you_could_not_know=later,
        assumptions=assumptions,
        alternatives=tuple(alternatives),
        confidence=confidence,
        warnings=tuple(warnings),
    )
