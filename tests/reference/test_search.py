"""Ranking passages for a question, with no model and no network."""

import pytest

from round_review.reference.corpus import Passage
from round_review.reference.search import build_index, search, tokenize


def passage(pid: str, text: str, kind: str = "note", tags: tuple[str, ...] = ()) -> Passage:
    return Passage(id=pid, kind=kind, title=pid, text=text, tags=tags)


class TestTokenize:
    def test_lowercases_and_splits_on_punctuation(self) -> None:
        assert tokenize("Viper's Toxic Screen, on Bind!") == ["viper", "toxic", "screen", "bind"]

    def test_drops_stopwords_and_single_characters(self) -> None:
        terms = tokenize("what should I have done at the site")
        assert "site" in terms
        assert not {"what", "should", "i", "have", "at", "the"} & set(terms)

    def test_keeps_numbers_that_matter(self) -> None:
        assert "3900" in tokenize("buy at 3900 credits")

    def test_an_empty_query_has_no_terms(self) -> None:
        assert tokenize("  the and of  ") == []


class TestSearch:
    def corpus(self) -> list[Passage]:
        return [
            passage(
                "viper", "Viper Toxic Screen wall lineup for the B site plant", tags=("viper",)
            ),
            passage("jett", "Jett Tailwind dash entry and updraft off angles", tags=("jett",)),
            passage("bind", "Bind teleporters Hookah and Showers callouts", tags=("bind",)),
            passage("ascent", "Ascent mid Market and Catwalk control", tags=("ascent",)),
            passage("crosshair", "Keep the crosshair at head height on the nearest corner"),
        ]

    def test_finds_the_passage_that_matches_the_question(self) -> None:
        index = build_index(self.corpus())
        hits = search(index, "how do I use the toxic screen wall on B?", limit=2)
        assert hits[0].passage.id == "viper"

    def test_ranks_by_relevance_not_by_order(self) -> None:
        index = build_index(self.corpus())
        hits = search(index, "crosshair head height", limit=3)
        assert hits[0].passage.id == "crosshair"

    def test_a_question_with_no_match_returns_nothing(self) -> None:
        index = build_index(self.corpus())
        assert search(index, "zzzz qqqq", limit=3) == []

    def test_an_empty_question_returns_nothing(self) -> None:
        assert search(build_index(self.corpus()), "   ", limit=3) == []

    def test_an_empty_corpus_returns_nothing(self) -> None:
        assert search(build_index([]), "anything", limit=3) == []

    def test_respects_the_limit(self) -> None:
        index = build_index(self.corpus())
        assert len(search(index, "callouts control entry wall crosshair", limit=2)) == 2

    def test_context_tags_lift_the_matching_agent_and_map(self) -> None:
        # all three passages match a term, so ordering is the only thing tags can change
        index = build_index(self.corpus())
        question = "site control callouts"
        neutral = [h.passage.id for h in search(index, question, limit=3, tags=())]
        boosted = [h.passage.id for h in search(index, question, limit=3, tags=("viper", "bind"))]
        assert set(neutral) == set(boosted) == {"viper", "bind", "ascent"}
        assert set(boosted[:2]) == {"viper", "bind"}
        assert neutral != boosted

    def test_a_tag_boost_cannot_promote_an_irrelevant_passage(self) -> None:
        index = build_index(self.corpus())
        hits = search(index, "crosshair head height", limit=1, tags=("jett",))
        assert hits[0].passage.id == "crosshair"

    def test_the_character_budget_is_respected(self) -> None:
        # ten passages of ~120 characters each, so a 500 character budget keeps about four
        long_passages = [passage(f"p{i:02d}", "wall lineup " * 10) for i in range(10)]
        hits = search(build_index(long_passages), "wall lineup", limit=10, max_chars=500)
        assert 1 < len(hits) < 10
        assert sum(len(h.passage.text) for h in hits) <= 500

    def test_a_single_passage_over_budget_is_still_returned(self) -> None:
        hits = search(build_index([passage("big", "wall " * 400)]), "wall", limit=3, max_chars=100)
        assert len(hits) == 1

    def test_hits_carry_their_score_and_are_ordered(self) -> None:
        hits = search(build_index(self.corpus()), "viper wall lineup plant", limit=3)
        assert hits[0].score > 0
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    def test_scoring_is_deterministic(self) -> None:
        index = build_index(self.corpus())
        first = [h.passage.id for h in search(index, "site control", limit=5)]
        second = [h.passage.id for h in search(index, "site control", limit=5)]
        assert first == second

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_limit_returns_nothing(self, bad: int) -> None:
        assert search(build_index(self.corpus()), "wall", limit=bad) == []
