"""Ability costs and verdicts: what makes "is it worth the money" answerable."""

from round_review.coaching.knowledge import find_agent, load_knowledge, render_agent_brief

KNOWLEDGE = load_knowledge()


def test_every_ability_of_every_agent_has_a_cost_and_a_moment() -> None:
    for brief in KNOWLEDGE.agents.values():
        for ability in brief.abilities:
            assert ability.cost, f"{brief.name}/{ability.name} has no cost"
            assert ability.when, f"{brief.name}/{ability.name} has no moment"


def test_basics_carry_a_verdict_and_ultimates_do_not_need_one() -> None:
    for brief in KNOWLEDGE.agents.values():
        for ability in brief.abilities:
            if "ult points" in ability.cost:
                continue
            assert ability.verdict, f"{brief.name}/{ability.name} has no verdict"


def test_every_agent_has_an_ultimate_cost() -> None:
    assert all(4 <= a.ult_points <= 10 for a in KNOWLEDGE.agents.values())


def test_the_free_signatures_are_marked_free() -> None:
    veto = find_agent(KNOWLEDGE.agents, "Veto")
    assert veto is not None
    interceptor = next(a for a in veto.abilities if a.name == "Interceptor")
    assert interceptor.cost.startswith("free")


def test_agents_are_tagged_for_eco_viability() -> None:
    chamber = find_agent(KNOWLEDGE.agents, "Chamber")
    astra = find_agent(KNOWLEDGE.agents, "Astra")
    assert chamber is not None and astra is not None
    assert chamber.eco == "strong"
    assert astra.eco == "crippled"


def test_the_agent_brief_states_what_each_ability_costs() -> None:
    veto = find_agent(KNOWLEDGE.agents, "Veto")
    assert veto is not None
    text = render_agent_brief(veto)
    assert "200" in text
    assert "Buy one always" in text
