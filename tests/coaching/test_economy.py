"""The economy brief: prices and thresholds a coach should not have to guess at."""

import pytest

from round_review.coaching.knowledge import load_knowledge, render_economy_brief
from round_review.errors import KnowledgeError

KNOWLEDGE = load_knowledge()


class TestLoading:
    def test_every_weapon_has_a_price_and_a_body_damage(self) -> None:
        for weapon in KNOWLEDGE.economy.weapons:
            assert weapon.cost >= 0
            assert weapon.body > 0

    def test_the_rifles_cost_what_they_cost(self) -> None:
        by_name = {w.name: w for w in KNOWLEDGE.economy.weapons}
        assert by_name["Vandal"].cost == 2900
        assert by_name["Operator"].cost == 4700
        assert by_name["Spectre"].cost == 1600

    def test_the_loss_ladder_is_the_published_one(self) -> None:
        assert KNOWLEDGE.economy.loss_ladder == (1900, 2400, 2900)

    def test_surviving_a_lost_round_pays_a_flat_amount(self) -> None:
        assert KNOWLEDGE.economy.survivor_payout == 1000

    def test_a_malformed_file_is_refused(self) -> None:
        from round_review.coaching.knowledge import parse_economy

        with pytest.raises(KnowledgeError):
            parse_economy({"weapons": [{"name": "Vandal"}]})


class TestShotsToKill:
    """The table that makes armor advice concrete instead of a feeling."""

    def test_a_vandal_needs_four_body_shots_through_any_shield(self) -> None:
        vandal = next(w for w in KNOWLEDGE.economy.weapons if w.name == "Vandal")
        assert vandal.shots_to_kill(100) == 3
        assert vandal.shots_to_kill(125) == 4
        assert vandal.shots_to_kill(150) == 4

    def test_a_spectre_costs_the_enemy_an_extra_bullet_per_shield_tier(self) -> None:
        spectre = next(w for w in KNOWLEDGE.economy.weapons if w.name == "Spectre")
        assert [spectre.shots_to_kill(hp) for hp in (100, 125, 150)] == [4, 5, 6]

    def test_an_operator_body_shot_kills_through_heavy(self) -> None:
        op = next(w for w in KNOWLEDGE.economy.weapons if w.name == "Operator")
        assert op.shots_to_kill(150) == 1


class TestBrief:
    def test_the_brief_names_prices_and_thresholds(self) -> None:
        text = render_economy_brief(KNOWLEDGE.economy)
        assert "Vandal 2900" in text or "Vandal 2,900" in text
        assert "3,900" in text or "3900" in text
        assert "1,900" in text or "1900" in text

    def test_the_brief_carries_the_survivor_penalty(self) -> None:
        text = render_economy_brief(KNOWLEDGE.economy)
        assert "survive" in text.lower()

    def test_the_brief_stays_within_its_budget(self) -> None:
        # About 1,500 tokens. It only ever goes out on a buy window, which carries the
        # economy checks alone and no situation timeline, so the room is there. If this
        # ever needs raising, cut a weapon note rather than the thresholds.
        assert len(render_economy_brief(KNOWLEDGE.economy)) < 7000

    def test_the_brief_does_not_pass_judgement_on_the_new_rifle(self) -> None:
        # Warden shipped in 13.06 with no meta consensus; the note says so rather than
        # telling the player whether to buy it.
        text = render_economy_brief(KNOWLEDGE.economy)
        assert "Warden" in text
        assert "too new" in text.lower()
