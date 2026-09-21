import json
from pathlib import Path
from typing import Any

import pytest

from round_review.coaching.parse import Finding, extract_json, parse_findings
from round_review.errors import ParseError
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(index=0, start_s=60.0, end_s=72.0, source="evenly_spaced")
SAMPLES = [FrameSample(0, 60.0 + i, Path(f"/f/w00_{i:03d}.jpg")) for i in range(12)]


def raw_finding(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timestamp_s": 64.4,
        "check_id": "crosshair.head_level",
        "category": "positioning",
        "observation": "You held a wide angle with two entrances uncleared.",
        "visible_evidence": "At t=64.0s the minimap shows no teammate on your right.",
        "information_available_to_player": "Minimap, round timer 1:05, no comms shown.",
        "information_revealed_later": "",
        "assumption_flags": ["enemy position"],
        "suggested_alternative": "Tighten the angle to expose one entrance at a time.",
        "confidence": 0.8,
    }
    return {**base, **overrides}


def wrap(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


def test_extract_json_strips_fences_and_prose() -> None:
    text = 'Sure! Here you go:\n```json\n{"findings": []}\n```\nHope that helps.'
    assert extract_json(text) == '{"findings": []}'


def test_extract_json_no_object_is_parse_error() -> None:
    with pytest.raises(ParseError, match="no JSON object"):
        extract_json("nothing here")


KNOWN = frozenset({"crosshair.head_level", "utility.unused_at_death"})


def test_happy_path() -> None:
    findings, warnings = parse_findings(wrap(raw_finding()), WINDOW, SAMPLES, KNOWN)
    assert warnings == []
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    assert f.check_id == "crosshair.head_level"
    assert f.timestamp_s == 64.4
    assert f.category == "positioning"
    assert f.assumption_flags == ("enemy position",)
    assert f.confidence == 0.8
    assert f.evidence_frame == Path("/f/w00_004.jpg")  # nearest sample to 64.4


def test_garbage_is_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_findings("the model rambled", WINDOW, SAMPLES)


def test_missing_findings_key_is_parse_error() -> None:
    with pytest.raises(ParseError, match="findings"):
        parse_findings('{"result": []}', WINDOW, SAMPLES)


def test_missing_required_field_is_parse_error() -> None:
    bad = raw_finding()
    del bad["visible_evidence"]
    with pytest.raises(ParseError, match="visible_evidence"):
        parse_findings(wrap(bad), WINDOW, SAMPLES)


def test_unknown_check_id_maps_to_other_with_warning() -> None:
    findings, warnings = parse_findings(
        wrap(raw_finding(check_id="made.up")), WINDOW, SAMPLES, KNOWN
    )
    assert findings[0].check_id == "other"
    assert any("made.up" in w for w in warnings)


def test_missing_check_id_is_parse_error() -> None:
    bad = raw_finding()
    del bad["check_id"]
    with pytest.raises(ParseError, match="check_id"):
        parse_findings(wrap(bad), WINDOW, SAMPLES, KNOWN)


def test_no_known_ids_accepts_anything() -> None:
    findings, warnings = parse_findings(wrap(raw_finding(check_id="whatever")), WINDOW, SAMPLES)
    assert findings[0].check_id == "whatever" and warnings == []


def test_unknown_category_maps_to_other() -> None:
    findings, _ = parse_findings(wrap(raw_finding(category="vibes")), WINDOW, SAMPLES)
    assert findings[0].category == "other"


def test_timestamp_outside_window_is_dropped_with_warning() -> None:
    findings, warnings = parse_findings(
        wrap(raw_finding(timestamp_s=10.0), raw_finding(timestamp_s=65.0)), WINDOW, SAMPLES
    )
    assert len(findings) == 1
    assert any("outside window" in w for w in warnings)


def test_confidence_is_clamped() -> None:
    findings, _ = parse_findings(wrap(raw_finding(confidence=1.7)), WINDOW, SAMPLES)
    assert findings[0].confidence == 1.0
    findings, _ = parse_findings(wrap(raw_finding(confidence=-2)), WINDOW, SAMPLES)
    assert findings[0].confidence == 0.0


def test_relies_on_later_info_downgrades_confidence() -> None:
    raw = raw_finding(information_revealed_later="An enemy was behind the box.", confidence=0.9)
    findings, warnings = parse_findings(wrap(raw), WINDOW, SAMPLES)
    assert findings[0].confidence == 0.5
    assert any("later" in w for w in warnings)


@pytest.mark.parametrize("value", ["", "none", "None.", "N/A", "nothing"])
def test_placeholder_later_info_does_not_downgrade(value: str) -> None:
    raw = raw_finding(information_revealed_later=value, confidence=0.9)
    findings, warnings = parse_findings(wrap(raw), WINDOW, SAMPLES)
    assert findings[0].confidence == 0.9
    assert warnings == []


def test_more_than_max_findings_truncated() -> None:
    findings, warnings = parse_findings(wrap(*[raw_finding()] * 7), WINDOW, SAMPLES)
    assert len(findings) == 4
    assert any("truncated" in w for w in warnings)


def test_empty_findings_is_valid() -> None:
    assert parse_findings(wrap(), WINDOW, SAMPLES) == ([], [])


@pytest.mark.parametrize("timestamp, expected", [(368.5, 368.517), (380.5, 380.5)])
def test_rounded_frame_caption_is_mapped_back_to_exact_sample(timestamp, expected) -> None:
    window = Window(0, 368.517, 380.517, "evenly_spaced")
    samples = [FrameSample(0, 368.517, Path("frame.jpg"))]
    findings, _ = parse_findings(wrap(raw_finding(timestamp_s=timestamp)), window, samples)
    assert findings[0].timestamp_s == expected


def test_rounding_does_not_admit_unsampled_out_of_window_timestamps() -> None:
    window = Window(0, 368.517, 380.517, "evenly_spaced")
    findings, warnings = parse_findings(wrap(raw_finding(timestamp_s=368.4)), window, [])
    assert findings == [] and any("outside window" in w for w in warnings)
