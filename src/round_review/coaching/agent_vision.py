"""Ask the model which agent the ability icons belong to, once, instead of per window.

On real footage the scene pass named a different agent in every one of eight windows, one
of them a player's name off the killfeed, while the player was on Veto throughout. That is
what asking a hard question badly looks like: "what is happening in this frame" buries the
agent among a hundred other things.

So ask it on its own, with only the four ability icons in the picture and the list of
agents to choose from, once per clip. A narrow question with a closed answer set is the
kind a small model gets right, and the answer is then locked for every window.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from round_review.coaching.knowledge import _slug
from round_review.coaching.parse import extract_json
from round_review.llm.client import build_chat_request
from round_review.llm.transport import Transport

AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["agent", "confidence"],
}

SYSTEM_PROMPT = (
    "You identify Valorant agents from their ability icons. Answer with JSON only, "
    "matching the schema you are given."
)


def build_agent_prompt(names: Sequence[str]) -> str:
    return (
        "These images are the ability icons from one player's HUD, in slot order.\n\n"
        "Which agent has exactly this set of abilities? Choose one name from this list:\n"
        f"{', '.join(names)}\n\n"
        'Answer "unknown" rather than guessing. A wrong name here makes every piece of '
        "advice that follows useless, so only answer when the icons actually match the "
        "agent you name."
    )


def identify_agent_with_model(
    transport: Transport,
    model: str,
    images_b64: Sequence[str],
    names: Sequence[str],
    timeout_s: float,
    min_confidence: float,
) -> tuple[str | None, float]:
    """The agent the model reads off the icons, or None when it will not commit."""
    if not images_b64:
        return None, 0.0
    request = build_chat_request(
        model,
        SYSTEM_PROMPT,
        build_agent_prompt(names),
        list(images_b64),
        AGENT_SCHEMA,
        timeout_s,
    )
    raw = extract_json(transport.chat(request).content)
    import json

    from round_review.errors import ParseError

    try:
        payload = json.loads(raw)
        answer = str(payload["agent"]).strip()
        confidence = float(payload["confidence"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"agent reply is not usable: {exc}") from exc

    if confidence < min_confidence:
        return None, 0.0
    # Only a name from the list counts. The model has answered with player names before.
    wanted = _slug(answer)
    for name in names:
        if _slug(name) == wanted:
            return name, confidence
    return None, 0.0
