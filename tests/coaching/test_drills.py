from round_review.coaching.knowledge import CATEGORY_IDS, load_drills
from round_review.errors import KnowledgeError


def test_every_category_has_a_rule_and_a_drill() -> None:
    drills = load_drills()
    for category in CATEGORY_IDS:
        practice = drills.for_category(category)
        assert practice is not None, category
        assert practice.rule and practice.drill, category


def test_the_checklist_categories_are_all_covered() -> None:
    from round_review.coaching.knowledge import load_checklist

    drills = load_drills()
    for category in load_checklist().categories:
        assert drills.for_category(category.id) is not None, category.id


def test_an_unknown_category_falls_back_to_the_general_advice() -> None:
    drills = load_drills()
    fallback = drills.for_category("made-up")
    assert fallback is not None
    assert fallback == drills.for_category("other")


def test_rules_are_imperative_and_short() -> None:
    # a rule has to be something you can hold in your head mid-round
    for category in CATEGORY_IDS:
        practice = load_drills().for_category(category)
        assert practice is not None
        assert len(practice.rule) < 120, category
        assert practice.rule.endswith(".")


def test_malformed_drills_are_a_knowledge_error() -> None:
    import pytest

    from round_review.coaching.knowledge import parse_drills

    with pytest.raises(KnowledgeError):
        parse_drills({"categories": {"crosshair": {"rule": "only a rule"}}})
