"""Machine-readable twin of the Markdown report, consumed by the desktop UI."""

from __future__ import annotations

import json
from dataclasses import asdict as _asdict
from pathlib import Path
from typing import Any

from round_review.coaching.knowledge import Checklist, load_checklist
from round_review.coaching.session import Habit
from round_review.report.markdown import Report, _relative, check_label

JSON_REPORT_FILENAME = "report.json"


def _habit_to_dict(habit: Habit, base_dir: Path, checklist: Checklist) -> dict[str, Any]:
    return {
        "check_id": habit.check_id,
        "check_label": check_label(checklist, habit.check_id),
        "category": habit.category,
        "count": habit.count,
        "windows": list(habit.windows),
        "score": habit.score,
        "mean_confidence": habit.mean_confidence,
        "timestamps": [f.timestamp_s for f in habit.instances],
        "observation": habit.lead.observation,
        "visible_evidence": habit.lead.visible_evidence,
        "information_revealed_later": habit.lead.information_revealed_later,
        "assumption_flags": list(habit.lead.assumption_flags),
        "focus": [_asdict(shape) for shape in habit.lead.focus],
        "suggested_alternative": habit.lead.suggested_alternative,
        "evidence_frame": _relative(habit.lead.evidence_frame, base_dir),
    }


def _summary_to_dict(report: Report, base_dir: Path) -> dict[str, Any]:
    summary = report.summary
    assert summary is not None
    checklist = load_checklist()
    return {
        "verdict": summary.verdict,
        "rank_focus": summary.rank_focus,
        "windows_reviewed": summary.windows_reviewed,
        "focus": [_habit_to_dict(h, base_dir, checklist) for h in summary.focus],
        "hindsight": [_habit_to_dict(h, base_dir, checklist) for h in summary.hindsight],
        "also_seen": [
            {
                "check_id": h.check_id,
                "check_label": check_label(checklist, h.check_id),
                "category": h.category,
                "count": h.count,
            }
            for h in summary.also_seen
        ],
        "strengths": [
            {
                **{k: v for k, v in _asdict(s).items() if k != "evidence_frame"},
                "check_label": check_label(checklist, s.check_id),
                "focus": [_asdict(shape) for shape in s.focus],
                "evidence_frame": _relative(s.evidence_frame, base_dir),
            }
            for s in summary.strengths
        ],
        "practice": _asdict(summary.practice) if summary.practice else None,
    }


def report_to_dict(report: Report, base_dir: Path) -> dict[str, Any]:
    checklist = load_checklist()
    rec = report.recording
    return {
        "recording": {
            "name": rec.path.name,
            "path": str(rec.path),
            "duration_s": rec.duration_s,
            "fps": rec.fps,
            "width": rec.width,
            "height": rec.height,
        },
        "generated_at": report.generated_at.isoformat(),
        "model": report.model,
        "partial": report.partial,
        "stopped_reason": report.stopped_reason,
        "summary": _summary_to_dict(report, base_dir) if report.summary else None,
        "windows": [
            {
                "index": r.window.index,
                "start_s": r.window.start_s,
                "end_s": r.window.end_s,
                "model_calls": r.model_calls,
                "context": _asdict(r.context),
                "abstained_reason": r.abstained_reason,
                "situation": r.situation.to_dict() if r.situation else None,
                "strengths": [
                    {
                        **{k: v for k, v in _asdict(s).items() if k != "evidence_frame"},
                        "focus": [_asdict(shape) for shape in s.focus],
                        "evidence_frame": _relative(s.evidence_frame, base_dir),
                    }
                    for s in r.strengths
                ],
                "findings": [
                    {
                        **{k: v for k, v in _asdict(f).items() if k != "evidence_frame"},
                        "check_label": check_label(checklist, f.check_id),
                        "assumption_flags": list(f.assumption_flags),
                        "focus": [_asdict(shape) for shape in f.focus],
                        "evidence_frame": _relative(f.evidence_frame, base_dir),
                    }
                    for f in r.findings
                ],
                "warnings": list(r.warnings),
            }
            for r in report.results
        ],
        "warnings": list(report.warnings),
    }


def write_report_json(report: Report, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / JSON_REPORT_FILENAME
    path.write_text(json.dumps(report_to_dict(report, out_dir), indent=2), encoding="utf-8")
    return path


def load_report_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
