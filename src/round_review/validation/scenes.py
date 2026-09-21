"""Scene-recognition validation: does the model read the screen correctly at all?

Coaching quality cannot be judged while the situation pass misclassifies the scene. This
module runs only the situation pass over hand-labelled moments and scores what it detected,
so model, frame-count and prompt changes can be compared on the same examples instead of
guessed at. See docs/coaching-quality.md.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from round_review.coaching.context import PlayerContext
from round_review.coaching.prompt import (
    SITUATION_SCHEMA,
    SITUATION_SYSTEM_PROMPT,
    build_situation_prompt,
)
from round_review.coaching.situation import PHASES, Situation, parse_situation
from round_review.errors import LabelsError, RoundReviewError
from round_review.llm.client import build_chat_request
from round_review.llm.transport import Transport
from round_review.video.frames import (
    FrameSample,
    encode_frame_b64,
    extract_single_frame,
)
from round_review.video.probe import CommandRunner, Recording
from round_review.video.windows import Window
from round_review.vision.hud import HudRead, constrain_phase

log = logging.getLogger(__name__)

UNREADABLE = "unreadable"
# What a human may write in a labels file. "unknown" is deliberately absent: a detection
# normalises it to "unreadable", so a case labelled "unknown" could never match. It is
# accepted as an alias below rather than offered as a choice.
LABELLED_PHASES: frozenset[str] = (frozenset(PHASES) - {"unknown"}) | {UNREADABLE}
PHASE_ALIASES: dict[str, str] = {"unknown": UNREADABLE}

ProbeFn = Callable[[Path], Recording]
ProgressFn = Callable[[int, int], None]
HudReaderFn = Callable[["SceneCase", Recording], HudRead]


@dataclass(frozen=True, slots=True)
class SceneCase:
    """One hand-labelled moment: what a human says is on screen at this timestamp."""

    clip: Path
    timestamp_s: float
    expected_phase: str
    notes: str = ""
    expected_agent: str | None = None
    expected_map: str | None = None
    expected_side: str | None = None


@dataclass(frozen=True, slots=True)
class SceneOutcome:
    case: SceneCase
    situation: Situation | None
    error: str | None
    frame: Path | None
    hud: HudRead | None = None
    # The phase after the deterministic HUD clock has had its say, and whether it changed.
    final_phase_override: str | None = None
    hud_corrected: bool = False

    @property
    def final_phase(self) -> str:
        return self.final_phase_override or self.detected_phase

    @property
    def final_ok(self) -> bool:
        return self.final_phase == self.case.expected_phase

    @property
    def detected_phase(self) -> str:
        if self.situation is None:
            return UNREADABLE
        return self.situation.phase or UNREADABLE

    @property
    def phase_ok(self) -> bool:
        return self.detected_phase == self.case.expected_phase

    @staticmethod
    def _same(expected: str | None, detected: str | None) -> bool | None:
        """None when the field was not labelled, so it is not scored."""
        if expected is None:
            return None
        return (detected or "").strip().lower() == expected.strip().lower()

    @property
    def agent_ok(self) -> bool | None:
        return self._same(
            self.case.expected_agent, self.situation.agent if self.situation else None
        )

    @property
    def map_ok(self) -> bool | None:
        return self._same(self.case.expected_map, self.situation.map if self.situation else None)

    @property
    def side_ok(self) -> bool | None:
        return self._same(self.case.expected_side, self.situation.side if self.situation else None)

    def field_results(self) -> dict[str, tuple[str, str, bool]]:
        """{field: (expected, detected, ok)} for every labelled field, phase included."""
        out: dict[str, tuple[str, str, bool]] = {
            "phase": (self.case.expected_phase, self.detected_phase, self.phase_ok)
        }
        for name, expected, detected, ok in (
            (
                "agent",
                self.case.expected_agent,
                self.situation.agent if self.situation else None,
                self.agent_ok,
            ),
            (
                "map",
                self.case.expected_map,
                self.situation.map if self.situation else None,
                self.map_ok,
            ),
            (
                "side",
                self.case.expected_side,
                self.situation.side if self.situation else None,
                self.side_ok,
            ),
        ):
            if expected is not None and ok is not None:
                out[name] = (expected, detected or UNREADABLE, ok)
        return out


def scene_accuracy(outcomes: Sequence[SceneOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if o.phase_ok) / len(outcomes)


@dataclass(frozen=True, slots=True)
class SceneReport:
    model: str
    frames_per_case: int
    outcomes: tuple[SceneOutcome, ...]

    def accuracy(self) -> float:
        return scene_accuracy(self.outcomes)

    def hud_accuracy(self) -> float:
        """Phase accuracy once the HUD clock has corrected what it can prove wrong."""
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.final_ok) / len(self.outcomes)

    def corrections(self) -> int:
        return sum(1 for o in self.outcomes if o.hud_corrected)

    def confusion(self) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for outcome in self.outcomes:
            key = (outcome.case.expected_phase, outcome.detected_phase)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def by_phase(self) -> dict[str, tuple[int, int]]:
        """{expected phase: (correct, total)}."""
        out: dict[str, tuple[int, int]] = {}
        for outcome in self.outcomes:
            correct, total = out.get(outcome.case.expected_phase, (0, 0))
            out[outcome.case.expected_phase] = (correct + int(outcome.phase_ok), total + 1)
        return out

    def collapsed_to(self) -> str | None:
        """The phase the model answered for every case, if it never varied. A model that
        always says the same thing scores well on a skewed set while recognising nothing."""
        detected = {o.detected_phase for o in self.outcomes}
        if len(self.outcomes) > 2 and len(detected) == 1:
            return next(iter(detected))
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "frames_per_case": self.frames_per_case,
            "accuracy": self.accuracy(),
            "hud_accuracy": self.hud_accuracy(),
            "hud_corrections": self.corrections(),
            "by_phase": {k: {"correct": c, "total": t} for k, (c, t) in self.by_phase().items()},
            "confusion": [
                {"expected": e, "detected": d, "count": n} for (e, d), n in self.confusion().items()
            ],
            "collapsed_to": self.collapsed_to(),
            "cases": [
                {
                    "clip": str(o.case.clip),
                    "t": o.case.timestamp_s,
                    "expected_phase": o.case.expected_phase,
                    "detected_phase": o.detected_phase,
                    "phase_ok": o.phase_ok,
                    "final_phase": o.final_phase,
                    "final_ok": o.final_ok,
                    "hud_clock": o.hud.clock_text if o.hud else None,
                    "hud_corrected": o.hud_corrected,
                    "fields": {
                        k: {"expected": e, "detected": d, "ok": ok}
                        for k, (e, d, ok) in o.field_results().items()
                    },
                    "summary": o.situation.summary if o.situation else None,
                    "frame": str(o.frame) if o.frame else None,
                    "error": o.error,
                    "notes": o.case.notes,
                }
                for o in self.outcomes
            ],
        }


# -- labels file -----------------------------------------------------------------------------


def load_cases(path: Path) -> list[SceneCase]:
    """Read a labels file, skipping placeholders that nobody has labelled yet."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabelsError(f"{path}: cannot read labels: {exc}") from exc
    raw_cases = payload.get("cases") if isinstance(payload, Mapping) else None
    if not isinstance(raw_cases, list):
        raise LabelsError(f"{path}: expected a 'cases' list")

    cases: list[SceneCase] = []
    unlabelled = 0
    for i, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, Mapping):
            raise LabelsError(f"{path}: case {i} is not an object")
        phase = str(raw.get("expected_phase", "")).strip()
        if not phase:
            unlabelled += 1
            continue
        phase = PHASE_ALIASES.get(phase, phase)
        if phase not in LABELLED_PHASES:
            raise LabelsError(
                f"{path}: case {i} has unknown expected_phase {phase!r}; "
                f"expected one of {sorted(LABELLED_PHASES)}"
            )
        try:
            cases.append(
                SceneCase(
                    clip=Path(str(raw["clip"])),
                    timestamp_s=float(raw["t"]),
                    expected_phase=phase,
                    notes=str(raw.get("notes", "")),
                    expected_agent=_optional(raw.get("expected_agent")),
                    expected_map=_optional(raw.get("expected_map")),
                    expected_side=_optional(raw.get("expected_side")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LabelsError(f"{path}: case {i} is malformed: {exc}") from exc

    if not cases:
        raise LabelsError(
            f"{path}: no labelled cases ({unlabelled} placeholder(s) still have an empty "
            "expected_phase). Look at the saved frames and fill each one in."
        )
    if unlabelled:
        log.info("%s: %d case(s) still unlabelled and skipped", path, unlabelled)
    return cases


def _optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def write_cases(path: Path, cases: Sequence[SceneCase]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phases": sorted(LABELLED_PHASES),
        "cases": [
            {
                "clip": str(c.clip),
                "t": c.timestamp_s,
                "expected_phase": c.expected_phase,
                "notes": c.notes,
                **({"expected_agent": c.expected_agent} if c.expected_agent else {}),
                **({"expected_map": c.expected_map} if c.expected_map else {}),
                **({"expected_side": c.expected_side} if c.expected_side else {}),
            }
            for c in cases
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def scaffold_cases(
    recording: Recording,
    every_s: float,
    ffmpeg: CommandRunner,
    width: int,
    frames_dir: Path,
) -> list[SceneCase]:
    """Cut a frame every `every_s` seconds and return unlabelled placeholders for each.

    The workflow is: scaffold, look at the saved frames, write the phase you actually see
    into the labels file, then validate.
    """
    if every_s <= 0:
        raise ValueError("every_s must be > 0")
    cases: list[SceneCase] = []
    timestamp = 0.0
    index = 0
    while timestamp < recording.duration_s:
        out = frames_dir / f"scene_{index:03d}.jpg"
        extract_single_frame(recording, timestamp, ffmpeg, width, out)
        cases.append(
            SceneCase(
                clip=recording.path,
                timestamp_s=timestamp,
                expected_phase="",
                notes=f"look at {out}",
            )
        )
        timestamp += every_s
        index += 1
    return cases


# -- running ---------------------------------------------------------------------------------


def frame_offsets(frames_per_case: int, spread_s: float) -> tuple[float, ...]:
    """Offsets around the labelled instant. One frame is the instant itself; more frames
    straddle it so the model can see motion."""
    if frames_per_case < 1:
        raise ValueError("frames_per_case must be >= 1")
    if frames_per_case == 1:
        return (0.0,)
    half = (frames_per_case - 1) / 2
    return tuple(round((i - half) * spread_s, 6) for i in range(frames_per_case))


def _read_scene(
    case: SceneCase,
    recording: Recording,
    transport: Transport,
    ffmpeg: CommandRunner,
    model: str,
    frames_per_case: int,
    spread_s: float,
    width: int,
    frames_dir: Path,
    timeout_s: float,
    hud_reader: HudReaderFn | None,
    buy_phase_max_s: float,
    hud_min_confidence: float,
) -> SceneOutcome:
    stem = f"{case.clip.stem}_{case.timestamp_s:.1f}".replace(".", "_")
    samples: list[FrameSample] = []
    for i, offset in enumerate(frame_offsets(frames_per_case, spread_s)):
        at = min(max(0.0, case.timestamp_s + offset), max(0.0, recording.duration_s - 0.05))
        out = frames_dir / f"{stem}_{i}.jpg"
        extract_single_frame(recording, at, ffmpeg, width, out)
        samples.append(FrameSample(0, at, out))

    first, last = samples[0].timestamp_s, samples[-1].timestamp_s
    window = Window(0, first, max(last, first + 0.1), "evenly_spaced")
    request = build_chat_request(
        model,
        SITUATION_SYSTEM_PROMPT,
        build_situation_prompt(window, samples, PlayerContext()),
        [encode_frame_b64(s.path) for s in samples],
        SITUATION_SCHEMA,
        timeout_s,
    )
    frame = samples[len(samples) // 2].path
    hud = hud_reader(case, recording) if hud_reader else None

    def scored(situation: Situation | None, error: str | None) -> SceneOutcome:
        model_phase = situation.phase if situation else None
        verdict = constrain_phase(model_phase, hud, buy_phase_max_s, hud_min_confidence)
        return SceneOutcome(
            case,
            situation,
            error,
            frame,
            hud,
            verdict.phase if verdict.overridden else None,
            verdict.overridden,
        )

    try:
        response = transport.chat(request)
    except RoundReviewError as exc:
        return scored(None, f"{type(exc).__name__}: {exc}")
    try:
        return scored(parse_situation(response.content), None)
    except RoundReviewError as exc:
        return scored(None, f"{type(exc).__name__}: {exc}")


def run_cases(
    cases: Sequence[SceneCase],
    *,
    transport: Transport,
    probe: ProbeFn,
    ffmpeg: CommandRunner,
    model: str,
    frames_per_case: int,
    spread_s: float,
    width: int,
    frames_dir: Path,
    timeout_s: float,
    on_progress: ProgressFn | None = None,
    hud_reader: HudReaderFn | None = None,
    buy_phase_max_s: float = 45.0,
    hud_min_confidence: float = 0.8,
) -> SceneReport:
    """Run the situation pass over every labelled case. Per-case failures are recorded as
    unreadable rather than raised, so one bad case never ends the run."""
    recordings: dict[Path, Recording] = {}
    outcomes: list[SceneOutcome] = []
    for i, case in enumerate(cases, start=1):
        if case.clip not in recordings:
            recordings[case.clip] = probe(case.clip)
        outcomes.append(
            _read_scene(
                case,
                recordings[case.clip],
                transport,
                ffmpeg,
                model,
                frames_per_case,
                spread_s,
                width,
                frames_dir,
                timeout_s,
                hud_reader,
                buy_phase_max_s,
                hud_min_confidence,
            )
        )
        if on_progress:
            on_progress(i, len(cases))
    return SceneReport(model, frames_per_case, tuple(outcomes))


# -- reporting -------------------------------------------------------------------------------


def render_scene_report(report: SceneReport) -> str:
    total = len(report.outcomes)
    correct = sum(1 for o in report.outcomes if o.phase_ok)
    plural = "" if report.frames_per_case == 1 else "s"
    lines = [
        f"Scene recognition: {correct}/{total} correct "
        f"({round(100 * report.accuracy())}%) with model {report.model}, "
        f"{report.frames_per_case} frame{plural} per case",
        "",
        "Expected phase        correct/total",
    ]
    for phase, (right, count) in sorted(report.by_phase().items()):
        lines.append(f"  {phase:<20} {right}/{count}")

    confusions = sorted(
        ((n, e, d) for (e, d), n in report.confusion().items() if e != d), reverse=True
    )
    if confusions:
        lines += ["", "Most common mistakes:"]
        lines += [f"  expected {e} -> detected {d} ({n}x)" for n, e, d in confusions[:5]]

    if report.corrections():
        lines += [
            "",
            f"With the HUD clock: {sum(1 for o in report.outcomes if o.final_ok)}/{total} correct "
            f"({round(100 * report.hud_accuracy())}%), {report.corrections()} correction(s) made "
            "from the round timer",
        ]
        wrong = [o for o in report.outcomes if o.hud_corrected and not o.final_ok]
        if wrong:
            lines.append(f"  {len(wrong)} correction(s) made the answer worse, not better")

    collapsed = report.collapsed_to()
    if collapsed:
        lines += [
            "",
            f"WARNING: the model always answered {collapsed!r}, for every single case. It is "
            "not reading the scene; treat any accuracy number here as meaningless.",
        ]

    failures = [o for o in report.outcomes if not o.phase_ok]
    if failures:
        lines += ["", "Failing cases:"]
        for o in failures:
            detail = o.error or (o.situation.summary if o.situation else "")
            lines.append(
                f"  {o.case.clip.name} t={o.case.timestamp_s:.1f}s "
                f"expected {o.case.expected_phase}, got {o.detected_phase}"
                + (f" - {detail}" if detail else "")
            )
            if o.frame:
                lines.append(f"      frame: {o.frame}")

    other_fields = {
        name: [o.field_results()[name] for o in report.outcomes if name in o.field_results()]
        for name in ("agent", "map", "side")
    }
    scored = {n: v for n, v in other_fields.items() if v}
    if scored:
        lines += ["", "Other labelled fields:"]
        for name, results in scored.items():
            right = sum(1 for _, _, ok in results if ok)
            lines.append(f"  {name:<20} {right}/{len(results)}")
    return "\n".join(lines)
