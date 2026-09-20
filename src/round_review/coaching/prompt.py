"""Prompt text and the JSON schema the model must return."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from round_review.video.frames import FrameSample
from round_review.video.windows import Window

CATEGORIES: tuple[str, ...] = (
    "positioning",
    "utility",
    "crosshair",
    "economy",
    "info",
    "timing",
    "other",
)

SYSTEM_PROMPT = """You are a tactical FPS coach reviewing sampled frames from a player's own \
Valorant recording. The frames are in chronological order and each is captioned with its \
timestamp.

Rules you must follow:
1. The player could only see what is on screen at each frame. Never judge a decision made \
in an earlier frame using anything that only becomes visible in a later frame.
2. Separate what you can actually see from what you are assuming. Enemy positions, ability \
cooldowns and team economy are assumptions unless the HUD or minimap shows them.
3. A reasonable decision that ended badly is not a mistake. Only report decisions that were \
poor given the information available at that moment.
4. Prefer fewer, well-evidenced findings over many weak ones. Return no findings if the \
frames do not support any.
5. Each finding needs one concrete, practical alternative the player can try next match.

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
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_s": {"type": "number"},
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


def build_user_prompt(window: Window, samples: Sequence[FrameSample], context: str | None) -> str:
    captions = "\n".join(f"Frame {i + 1}: t={s.timestamp_s:.1f}s" for i, s in enumerate(samples))
    context_line = f"Player context: {context}\n\n" if context else ""
    return (
        f"{context_line}"
        f"This window covers t={window.start_s:.1f}s to t={window.end_s:.1f}s of the recording. "
        f"You are given {len(samples)} frames, attached in this order:\n{captions}\n\n"
        "Review the player's decisions in this window. For each finding give the timestamp "
        "(within this window), the category, what you observed, the visible evidence with the "
        "frame timestamp it comes from, what information the player had at that moment, what "
        "only became known later (empty string if nothing), the assumptions you are making, one "
        "suggested alternative, and your confidence from 0 to 1."
    )
