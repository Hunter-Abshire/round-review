"""A review that only ever criticises is not how coaching works, and it gives the player no
signal about which habits to keep."""

import json
from pathlib import Path
from typing import Any

import pytest

from round_review.coaching.parse import Strength, parse_strengths
from round_review.errors import ParseError
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(index=0, start_s=60.0, end_s=72.0, source="tiled")
SAMPLES = [FrameSample(0, 60.0 + i, Path(f"/f/w00_{i:03d}.jpg")) for i in range(12)]
KNOWN = frozenset({"crosshair.head_level", "utility.has_purpose", "trading.stay_tradeable"})


def raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timestamp_s": 64.0,
        "check_id": "crosshair.head_level",
        "category": "crosshair",
        "observation": "Held the crosshair at head height on the door frame while repositioning.",
        "visible_evidence": "At t=64.0s the crosshair sits on the door frame line, not the floor.",
        "why_it_worked": "The first shot needed no vertical correction when the enemy appeared.",
        "confidence": 0.8,
    }
    return {**base, **overrides}


def wrap(*strengths: dict[str, Any], findings: list[Any] | None = None) -> str:
    return json.dumps({"findings": findings or [], "strengths": list(strengths)})


def test_parses_a_strength() -> None:
    strengths, warnings = parse_strengths(wrap(raw()), WINDOW, SAMPLES, KNOWN)
    assert warnings == []
    (strength,) = strengths
    assert isinstance(strength, Strength)
    assert strength.check_id == "crosshair.head_level"
    assert strength.category == "crosshair"
    assert strength.why_it_worked.startswith("The first shot")
    assert strength.evidence_frame == Path("/f/w00_004.jpg")


def test_a_reply_with_no_strengths_is_fine() -> None:
    assert parse_strengths(wrap(), WINDOW, SAMPLES, KNOWN) == ([], [])
    assert parse_strengths('{"findings": []}', WINDOW, SAMPLES, KNOWN) == ([], [])


def test_missing_required_field_is_a_warning_not_a_failure() -> None:
    # praise is a bonus; a malformed strength must never cost the player their findings
    bad = raw()
    del bad["why_it_worked"]
    strengths, warnings = parse_strengths(wrap(bad), WINDOW, SAMPLES, KNOWN)
    assert strengths == []
    assert any("why_it_worked" in w for w in warnings)


def test_strengths_outside_the_window_are_dropped() -> None:
    strengths, warnings = parse_strengths(
        wrap(raw(timestamp_s=5.0), raw(timestamp_s=65.0)), WINDOW, SAMPLES, KNOWN
    )
    assert len(strengths) == 1
    assert any("outside window" in w for w in warnings)


def test_an_unknown_check_is_kept_but_relabelled() -> None:
    strengths, warnings = parse_strengths(wrap(raw(check_id="made.up")), WINDOW, SAMPLES, KNOWN)
    assert strengths[0].check_id == "other"
    assert any("made.up" in w for w in warnings)


def test_at_most_two_strengths_survive_per_window() -> None:
    strengths, warnings = parse_strengths(wrap(*[raw()] * 5), WINDOW, SAMPLES, KNOWN)
    assert len(strengths) == 2
    assert any("truncated" in w for w in warnings)


def test_vague_praise_is_rejected() -> None:
    # "nice job" reinforces nothing; a strength must name the behaviour
    for vague in ("Good job.", "nice", "Well played!", "Solid round."):
        strengths, warnings = parse_strengths(wrap(raw(observation=vague)), WINDOW, SAMPLES, KNOWN)
        assert strengths == [], vague
        assert any("too vague" in w for w in warnings)


def test_confidence_is_clamped() -> None:
    strengths, _ = parse_strengths(wrap(raw(confidence=2.0)), WINDOW, SAMPLES, KNOWN)
    assert strengths[0].confidence == 1.0


def test_garbage_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_strengths("not json at all", WINDOW, SAMPLES, KNOWN)
