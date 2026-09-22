"""The ability guard: a finding may only name abilities the player actually has."""

from round_review.coaching.abilities import ability_owners, foreign_abilities
from round_review.coaching.knowledge import load_knowledge

KNOWLEDGE = load_knowledge()


def test_owners_map_every_ability_to_one_agent() -> None:
    owners = ability_owners(KNOWLEDGE.agents)
    assert owners["shrouded step"] == "Omen"
    assert owners["chokehold"] == "Veto"
    # Every ability in the briefs is claimed exactly once, or the guard would misfire.
    total = sum(len(a.abilities) for a in KNOWLEDGE.agents.values())
    assert len(owners) == total


def test_another_agents_ability_is_foreign() -> None:
    text = "Use Shrouded Step to reach the off-angle instead."
    assert foreign_abilities(text, "Veto", KNOWLEDGE.agents) == ("Shrouded Step",)


def test_own_ability_is_not_foreign() -> None:
    assert foreign_abilities("Chokehold the choke first.", "Veto", KNOWLEDGE.agents) == ()


def test_unknown_agent_flags_nothing() -> None:
    assert foreign_abilities("Use Shrouded Step.", None, KNOWLEDGE.agents) == ()


def test_agent_name_matching_is_forgiving() -> None:
    assert foreign_abilities("Use Shrouded Step.", "veto", KNOWLEDGE.agents) == ("Shrouded Step",)


def test_multi_word_names_match_case_insensitively() -> None:
    assert foreign_abilities("use shrouded step here", "Veto", KNOWLEDGE.agents) == (
        "Shrouded Step",
    )


def test_single_word_names_need_the_capital() -> None:
    # "seize" and "meddle" are ordinary English; only the proper noun is an ability.
    assert foreign_abilities("do not seize the angle", "Veto", KNOWLEDGE.agents) == ()
    assert foreign_abilities("Seize the corner first", "Veto", KNOWLEDGE.agents) == ("Seize",)


def test_substrings_of_longer_words_are_not_matches() -> None:
    assert foreign_abilities("Seizeing is not a word", "Veto", KNOWLEDGE.agents) == ()


def test_several_foreign_abilities_are_all_reported() -> None:
    text = "Throw Paranoia, then Shrouded Step through it."
    assert foreign_abilities(text, "Veto", KNOWLEDGE.agents) == ("Paranoia", "Shrouded Step")
