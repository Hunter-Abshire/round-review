"""Turn model output into validated Findings. Lenient on formatting, strict on content."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from round_review.coaching.prompt import CATEGORIES, MAX_FINDINGS_PER_WINDOW
from round_review.errors import ParseError
from round_review.video.frames import FrameSample
from round_review.video.windows import Window

# Confidence above this is downgraded when the finding leans on later-revealed information.
HINDSIGHT_CONFIDENCE_CEILING = 0.5
HINDSIGHT_TRIGGER = 0.7
PLACEHOLDER_LATER_INFO: frozenset[str] = frozenset({"", "none", "n/a", "na", "nothing", "-"})

MAX_STRENGTHS_PER_WINDOW = 2
# Shapes the model may draw over the frame. More than a few is clutter, not emphasis.
MAX_FOCUS_SHAPES = 4
SHAPE_KINDS: frozenset[str] = frozenset({"box", "point", "arrow"})

# Praise has to name the behaviour to reinforce it. Anything this short is filler.
MIN_PRAISE_CHARACTERS = 25
VAGUE_PRAISE: frozenset[str] = frozenset(
    {"good job", "nice", "nice job", "well played", "solid round", "great", "good", "well done"}
)

STRENGTH_FIELDS: tuple[str, ...] = (
    "timestamp_s",
    "check_id",
    "category",
    "observation",
    "visible_evidence",
    "why_it_worked",
    "confidence",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "timestamp_s",
    "check_id",
    "category",
    "observation",
    "visible_evidence",
    "information_available_to_player",
    "information_revealed_later",
    "assumption_flags",
    "suggested_alternative",
    "confidence",
)


@dataclass(frozen=True, slots=True)
class Shape:
    """Where on the frame the model is pointing, in fractions of width and height.

    A box uses x, y, w, h; a point uses x, y; an arrow runs from x, y to x2, y2. The model
    is estimating from a downscaled frame, so treat these as emphasis, not measurement.
    """

    kind: str
    label: str
    x: float
    y: float
    w: float = 0.0
    h: float = 0.0
    x2: float = 0.0
    y2: float = 0.0


def _fraction(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def parse_shapes(raw: object) -> tuple[tuple[Shape, ...], list[str]]:
    """Validate drawn shapes. A bad shape is dropped with a warning and never costs the
    finding it came with: the words matter more than the drawing."""
    if raw in (None, ""):
        return (), []
    if not isinstance(raw, list):
        return (), ["focus was not a list of shapes; ignored"]
    shapes: list[Shape] = []
    warnings: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            warnings.append("dropped a focus shape that was not an object")
            continue
        kind = str(item.get("kind", ""))
        label = str(item.get("label", "")).strip()
        if kind not in SHAPE_KINDS:
            warnings.append(f"dropped a focus shape of unknown kind {kind!r}")
            continue
        if not label:
            warnings.append(f"dropped a focus {kind} with no label")
            continue
        x, y = _fraction(item.get("x")), _fraction(item.get("y"))
        if x is None or y is None:
            warnings.append(f"dropped a focus {kind} outside the frame")
            continue
        if kind == "box":
            w, h = _fraction(item.get("w")), _fraction(item.get("h"))
            if not w or not h or x + w > 1.0 or y + h > 1.0:
                warnings.append("dropped a focus box that falls outside the frame")
                continue
            shapes.append(Shape(kind, label, x, y, w=w, h=h))
        elif kind == "arrow":
            x2, y2 = _fraction(item.get("x2")), _fraction(item.get("y2"))
            if x2 is None or y2 is None:
                warnings.append("dropped a focus arrow without an end point")
                continue
            shapes.append(Shape(kind, label, x, y, x2=x2, y2=y2))
        else:
            shapes.append(Shape(kind, label, x, y))
    if len(shapes) > MAX_FOCUS_SHAPES:
        warnings.append(f"more than {MAX_FOCUS_SHAPES} focus shapes offered; truncated")
        shapes = shapes[:MAX_FOCUS_SHAPES]
    return tuple(shapes), warnings


@dataclass(frozen=True, slots=True)
class Finding:
    timestamp_s: float
    check_id: str
    category: str
    observation: str
    visible_evidence: str
    information_available_to_player: str
    information_revealed_later: str
    assumption_flags: tuple[str, ...]
    suggested_alternative: str
    confidence: float
    evidence_frame: Path | None
    # Optional shapes drawn over the evidence frame to show what is being talked about.
    focus: tuple[Shape, ...] = ()


@dataclass(frozen=True, slots=True)
class Strength:
    """Something the player did right, worth reinforcing. Kept separate from findings so
    criticism and praise never dilute each other."""

    timestamp_s: float
    check_id: str
    category: str
    observation: str
    visible_evidence: str
    why_it_worked: str
    confidence: float
    evidence_frame: Path | None
    # Optional shapes drawn over the evidence frame to show what is being talked about.
    focus: tuple[Shape, ...] = ()


def extract_json(text: str) -> str:
    """Slice the first '{' to the last '}' so fences and prose around the JSON are ignored."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ParseError("model reply contains no JSON object")
    return text[start : end + 1]


def _nearest_frame(timestamp_s: float, samples: Sequence[FrameSample]) -> Path | None:
    if not samples:
        return None
    return min(samples, key=lambda s: abs(s.timestamp_s - timestamp_s)).path


def _is_placeholder(text: str) -> bool:
    return text.strip().rstrip(".").lower() in PLACEHOLDER_LATER_INFO


def _validate(
    raw: dict[str, Any],
    window: Window,
    samples: Sequence[FrameSample],
    check_ids: frozenset[str] | None,
) -> tuple[Finding | None, list[str]]:
    missing = [k for k in REQUIRED_FIELDS if k not in raw]
    if missing:
        raise ParseError(f"finding missing required field(s): {', '.join(missing)}")
    warnings: list[str] = []
    try:
        timestamp = float(raw["timestamp_s"])
        confidence = min(1.0, max(0.0, float(raw["confidence"])))
        flags = tuple(str(f) for f in raw["assumption_flags"])
    except (TypeError, ValueError) as exc:
        raise ParseError(f"finding has a malformed numeric or list field: {exc}") from exc

    # Captions use tenths of a second. Restore a caption to its exact sampled time
    # before enforcing bounds; never give arbitrary out-of-window times a tolerance.
    for sample in samples:
        if timestamp == round(sample.timestamp_s, 1):
            timestamp = sample.timestamp_s
            break
    if not (window.start_s <= timestamp <= window.end_s):
        warnings.append(
            f"dropped finding at t={timestamp:.1f}s: outside window "
            f"{window.start_s:.1f}-{window.end_s:.1f}s"
        )
        return None, warnings

    category = str(raw["category"])
    if category not in CATEGORIES:
        category = "other"

    check_id = str(raw["check_id"]).strip()
    if check_ids is not None and check_id not in check_ids:
        warnings.append(
            f"finding at t={timestamp:.1f}s cited unknown check {check_id!r}; set to other"
        )
        check_id = "other"

    later = str(raw["information_revealed_later"])
    if not _is_placeholder(later) and confidence > HINDSIGHT_TRIGGER:
        warnings.append(
            f"finding at t={timestamp:.1f}s relies on later-revealed information; "
            f"confidence downgraded from {confidence:.2f} to {HINDSIGHT_CONFIDENCE_CEILING}"
        )
        confidence = HINDSIGHT_CONFIDENCE_CEILING

    shapes, shape_warnings = parse_shapes(raw.get("focus"))
    warnings.extend(f"finding at t={timestamp:.1f}s: {w}" for w in shape_warnings)

    finding = Finding(
        timestamp_s=timestamp,
        check_id=check_id,
        category=category,
        observation=str(raw["observation"]),
        visible_evidence=str(raw["visible_evidence"]),
        information_available_to_player=str(raw["information_available_to_player"]),
        information_revealed_later=later,
        assumption_flags=flags,
        suggested_alternative=str(raw["suggested_alternative"]),
        confidence=confidence,
        evidence_frame=_nearest_frame(timestamp, samples),
        focus=shapes,
    )
    return finding, warnings


def parse_findings(
    text: str,
    window: Window,
    samples: Sequence[FrameSample],
    check_ids: frozenset[str] | None = None,
) -> tuple[list[Finding], list[str]]:
    """Parse the model reply. Returns (findings, warnings). Raises ParseError when the reply
    is structurally unusable; individual bad findings become warnings instead. When
    `check_ids` is given, unknown ids are mapped to "other" with a warning."""
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise ParseError(f"model reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or "findings" not in payload:
        raise ParseError('model reply has no "findings" key')
    raw_findings = payload["findings"]
    if not isinstance(raw_findings, list):
        raise ParseError('"findings" is not a list')

    findings: list[Finding] = []
    warnings: list[str] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            raise ParseError("finding is not an object")
        finding, finding_warnings = _validate(raw, window, samples, check_ids)
        warnings.extend(finding_warnings)
        if finding is not None:
            findings.append(finding)

    if len(findings) > MAX_FINDINGS_PER_WINDOW:
        warnings.append(
            f"model returned {len(findings)} findings; truncated to {MAX_FINDINGS_PER_WINDOW}"
        )
        findings = findings[:MAX_FINDINGS_PER_WINDOW]
    return findings, warnings


def _is_vague(text: str) -> bool:
    stripped = text.strip().rstrip(".!").lower()
    return stripped in VAGUE_PRAISE or len(text.strip()) < MIN_PRAISE_CHARACTERS


def parse_strengths(
    text: str,
    window: Window,
    samples: Sequence[FrameSample],
    check_ids: frozenset[str] | None = None,
) -> tuple[list[Strength], list[str]]:
    """Parse the optional `strengths` array. A malformed strength is a warning, never an
    error: praise is a bonus and must not be able to cost the player their findings."""
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise ParseError(f"model reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("model reply is not an object")
    raw_strengths = payload.get("strengths") or []
    if not isinstance(raw_strengths, list):
        return [], ["strengths was not a list; ignored"]

    strengths: list[Strength] = []
    warnings: list[str] = []
    for raw in raw_strengths:
        if not isinstance(raw, dict):
            warnings.append("dropped a strength that was not an object")
            continue
        missing = [k for k in STRENGTH_FIELDS if k not in raw]
        if missing:
            warnings.append(f"dropped a strength missing {', '.join(missing)}")
            continue
        try:
            timestamp = float(raw["timestamp_s"])
            confidence = min(1.0, max(0.0, float(raw["confidence"])))
        except (TypeError, ValueError) as exc:
            warnings.append(f"dropped a strength with a malformed number: {exc}")
            continue
        if not (window.start_s <= timestamp <= window.end_s):
            warnings.append(
                f"dropped strength at t={timestamp:.1f}s: outside window "
                f"{window.start_s:.1f}-{window.end_s:.1f}s"
            )
            continue
        observation = str(raw["observation"])
        if _is_vague(observation):
            warnings.append(
                f"dropped strength at t={timestamp:.1f}s: {observation!r} is too vague to "
                "reinforce anything"
            )
            continue
        check_id = str(raw["check_id"]).strip()
        if check_ids is not None and check_id not in check_ids:
            warnings.append(
                f"strength at t={timestamp:.1f}s cited unknown check {check_id!r}; set to other"
            )
            check_id = "other"
        category = str(raw["category"])
        if category not in CATEGORIES:
            category = "other"
        shapes, shape_warnings = parse_shapes(raw.get("focus"))
        warnings.extend(f"strength at t={timestamp:.1f}s: {w}" for w in shape_warnings)
        strengths.append(
            Strength(
                timestamp_s=timestamp,
                check_id=check_id,
                category=category,
                observation=observation,
                visible_evidence=str(raw["visible_evidence"]),
                why_it_worked=str(raw["why_it_worked"]),
                confidence=confidence,
                evidence_frame=_nearest_frame(timestamp, samples),
                focus=shapes,
            )
        )

    if len(strengths) > MAX_STRENGTHS_PER_WINDOW:
        warnings.append(
            f"model returned {len(strengths)} strengths; truncated to {MAX_STRENGTHS_PER_WINDOW}"
        )
        strengths = strengths[:MAX_STRENGTHS_PER_WINDOW]
    return strengths, warnings
