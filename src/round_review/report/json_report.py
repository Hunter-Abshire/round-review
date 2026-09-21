"""Machine-readable twin of the Markdown report, consumed by the desktop UI."""

from __future__ import annotations

import json
from dataclasses import asdict as _asdict
from pathlib import Path
from typing import Any

from round_review.coaching.knowledge import load_checklist
from round_review.report.markdown import Report, _relative, check_label

JSON_REPORT_FILENAME = "report.json"


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
        "windows": [
            {
                "index": r.window.index,
                "start_s": r.window.start_s,
                "end_s": r.window.end_s,
                "model_calls": r.model_calls,
                "context": _asdict(r.context),
                "abstained_reason": r.abstained_reason,
                "situation": r.situation.to_dict() if r.situation else None,
                "findings": [
                    {
                        **{k: v for k, v in _asdict(f).items() if k != "evidence_frame"},
                        "check_label": check_label(checklist, f.check_id),
                        "assumption_flags": list(f.assumption_flags),
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
