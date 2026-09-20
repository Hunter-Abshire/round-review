import json
from pathlib import Path

from round_review.coaching.prompt import FINDING_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from round_review.video.frames import FrameSample
from round_review.video.windows import Window


def test_schema_is_json_serialisable_and_requires_anti_hindsight_fields() -> None:
    json.dumps(FINDING_SCHEMA)
    finding = FINDING_SCHEMA["properties"]["findings"]["items"]
    for key in (
        "timestamp_s",
        "category",
        "observation",
        "visible_evidence",
        "information_available_to_player",
        "information_revealed_later",
        "assumption_flags",
        "suggested_alternative",
        "confidence",
    ):
        assert key in finding["required"], key


def test_system_prompt_states_the_rules() -> None:
    lower = SYSTEM_PROMPT.lower()
    assert "later" in lower and "earlier" in lower
    assert "json" in lower


def test_user_prompt_captions_frames_in_order() -> None:
    window = Window(index=1, start_s=100.0, end_s=112.0, source="evenly_spaced")
    samples = [FrameSample(1, 100.0 + i, Path(f"/f/{i}.jpg")) for i in range(3)]
    text = build_user_prompt(window, samples, context="Gold 2, Jett, Ascent")
    assert "Gold 2, Jett, Ascent" in text
    assert text.index("t=100.0s") < text.index("t=101.0s") < text.index("t=102.0s")
    assert "100.0" in text and "112.0" in text
    assert "3 frames" in text


def test_user_prompt_without_context() -> None:
    window = Window(index=0, start_s=0.0, end_s=12.0, source="evenly_spaced")
    text = build_user_prompt(window, [FrameSample(0, 0.0, Path("/f/0.jpg"))], context=None)
    assert "t=0.0s" in text
