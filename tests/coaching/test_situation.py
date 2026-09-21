import json

import pytest

from round_review.coaching.situation import Situation, parse_situation
from round_review.errors import ParseError


def raw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "agent": "Jett",
        "map": "Ascent",
        "side": "attack",
        "phase": "early",
        "weapon": "Vandal",
        "abilities_available": ["Tailwind", "Updraft"],
        "credits": 3900,
        "teammates_alive": 4,
        "enemies_visible": 1,
        "timeline": [
            {"t": 100.0, "event": "walking A main"},
            {"t": 104.0, "event": "peeks Heaven"},
        ],
        "summary": "Entering A with dash up.",
    }
    return {**base, **overrides}


def test_parse_happy_path() -> None:
    s = parse_situation(json.dumps(raw()))
    assert isinstance(s, Situation)
    assert s.agent == "Jett" and s.map == "Ascent" and s.side == "attack" and s.phase == "early"
    assert s.abilities_available == ("Tailwind", "Updraft")
    assert s.timeline == ((100.0, "walking A main"), (104.0, "peeks Heaven"))
    assert s.credits == 3900


def test_unknowns_become_none() -> None:
    s = parse_situation(
        json.dumps(raw(agent="unknown", map="", side="spectator", phase="???", credits=None))
    )
    assert (
        s.agent is None
        and s.map is None
        and s.side is None
        and s.phase is None
        and s.credits is None
    )


def test_to_context_and_describe() -> None:
    s = parse_situation(json.dumps(raw()))
    ctx = s.to_context()
    assert ctx.agent == "Jett" and ctx.map == "Ascent" and ctx.side == "attack" and ctx.rank is None
    text = s.describe()
    assert "Vandal" in text and "Tailwind" in text and "t=104.0s peeks Heaven" in text


def test_garbage_is_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_situation("nope")


def test_missing_summary_is_parse_error() -> None:
    bad = raw()
    del bad["summary"]
    with pytest.raises(ParseError, match="summary"):
        parse_situation(json.dumps(bad))


def test_to_dict_round_trips() -> None:
    s = parse_situation(json.dumps(raw()))
    assert parse_situation(json.dumps(s.to_dict())) == s
