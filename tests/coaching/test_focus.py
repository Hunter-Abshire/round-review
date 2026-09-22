"""Shapes the model draws over the frame to show what it is talking about."""

import json
from pathlib import Path
from typing import Any

import pytest

from round_review.coaching.parse import MAX_FOCUS_SHAPES, Shape, parse_findings
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

WINDOW = Window(0, 60.0, 72.0, "tiled")
SAMPLES = [FrameSample(0, 60.0 + i, Path(f"/f/w00_{i:03d}.jpg")) for i in range(12)]


def finding(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timestamp_s": 64.0,
        "check_id": "positioning.cover",
        "category": "positioning",
        "observation": "o",
        "visible_evidence": "v",
        "information_available_to_player": "i",
        "information_revealed_later": "",
        "assumption_flags": [],
        "suggested_alternative": "s",
        "confidence": 0.8,
    }
    return {**base, **overrides}


def wrap(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


def box(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "box",
        "label": "the exposed angle",
        "x": 0.4,
        "y": 0.3,
        "w": 0.2,
        "h": 0.25,
        **overrides,
    }


class TestShapes:
    def test_a_box_is_parsed_with_its_label(self) -> None:
        found, _ = parse_findings(wrap(finding(focus=[box()])), WINDOW, SAMPLES)
        (shape,) = found[0].focus
        assert isinstance(shape, Shape)
        assert shape.kind == "box"
        assert shape.label == "the exposed angle"
        assert (shape.x, shape.y, shape.w, shape.h) == (0.4, 0.3, 0.2, 0.25)

    def test_a_point_and_an_arrow_are_parsed(self) -> None:
        shapes = [
            {"kind": "point", "label": "here", "x": 0.5, "y": 0.5},
            {"kind": "arrow", "label": "move this way", "x": 0.2, "y": 0.2, "x2": 0.8, "y2": 0.6},
        ]
        found, _ = parse_findings(wrap(finding(focus=shapes)), WINDOW, SAMPLES)
        assert [s.kind for s in found[0].focus] == ["point", "arrow"]
        assert found[0].focus[1].x2 == 0.8

    def test_no_focus_is_normal(self) -> None:
        found, warnings = parse_findings(wrap(finding()), WINDOW, SAMPLES)
        assert found[0].focus == ()
        assert warnings == []

    @pytest.mark.parametrize(
        "bad",
        [
            {"kind": "box", "label": "x", "x": -0.1, "y": 0.1, "w": 0.2, "h": 0.2},
            {"kind": "box", "label": "x", "x": 0.9, "y": 0.1, "w": 0.5, "h": 0.2},
            {"kind": "box", "label": "x", "x": 0.1, "y": 0.1, "w": 0.0, "h": 0.2},
            {"kind": "point", "label": "x", "x": 1.5, "y": 0.1},
            {"kind": "blob", "label": "x", "x": 0.1, "y": 0.1},
            {"kind": "arrow", "label": "x", "x": 0.1, "y": 0.1},
        ],
    )
    def test_a_shape_outside_the_frame_or_malformed_is_dropped(self, bad: dict[str, Any]) -> None:
        found, warnings = parse_findings(wrap(finding(focus=[bad])), WINDOW, SAMPLES)
        assert found[0].focus == ()
        assert any("focus" in w for w in warnings)

    def test_a_bad_shape_does_not_cost_the_finding(self) -> None:
        found, _ = parse_findings(wrap(finding(focus=[{"kind": "blob"}])), WINDOW, SAMPLES)
        assert len(found) == 1
        assert found[0].observation == "o"

    def test_too_many_shapes_are_truncated(self) -> None:
        found, warnings = parse_findings(wrap(finding(focus=[box()] * 8)), WINDOW, SAMPLES)
        assert len(found[0].focus) == MAX_FOCUS_SHAPES
        assert any("focus" in w for w in warnings)

    def test_a_label_is_required_so_a_shape_always_explains_itself(self) -> None:
        found, warnings = parse_findings(wrap(finding(focus=[box(label="")])), WINDOW, SAMPLES)
        assert found[0].focus == ()
        assert any("label" in w for w in warnings)

    def test_focus_is_not_a_list(self) -> None:
        found, warnings = parse_findings(wrap(finding(focus="nope")), WINDOW, SAMPLES)
        assert found[0].focus == ()
        assert any("focus" in w for w in warnings)
