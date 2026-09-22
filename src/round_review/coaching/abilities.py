"""Check that a finding only names abilities the player actually has.

A small vision model that misreads the agent portrait will happily coach the wrong
character, and "use Shrouded Step" is useless advice to someone playing Veto. Ability
ownership is a fact in the bundled briefs, so this is a lookup rather than something the
model should be trusted to get right.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from round_review.coaching.knowledge import AgentBrief, find_agent


def ability_owners(agents: Mapping[str, AgentBrief]) -> dict[str, str]:
    """Lowercased ability name -> the name of the agent it belongs to."""
    return {
        name.lower(): brief.name
        for brief in agents.values()
        for _key, name, _purpose in brief.abilities
    }


def _pattern(name: str) -> re.Pattern[str]:
    # Single-word abilities collide with ordinary English ("seize", "meddle", "cove"), so
    # only the capitalised proper noun counts. Multi-word names are unambiguous either way.
    flags = 0 if len(name.split()) == 1 else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", flags)


def foreign_abilities(
    text: str, agent: str | None, agents: Mapping[str, AgentBrief]
) -> tuple[str, ...]:
    """Ability names in `text` that belong to some agent other than `agent`.

    Returns nothing when the agent is unknown: without a reference point every ability is
    equally plausible, and guessing would drop good findings.
    """
    brief = find_agent(agents, agent)
    if brief is None:
        return ()
    own = {name.lower() for _key, name, _purpose in brief.abilities}
    found = {
        canonical
        for lowered, canonical in (
            (name.lower(), name)
            for other in agents.values()
            for _key, name, _purpose in other.abilities
        )
        if lowered not in own and _pattern(canonical).search(text)
    }
    return tuple(sorted(found))
