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
