"""Render a Report to Markdown. Evidence frames are linked relative to the report file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from round_review.coaching.parse import Finding
from round_review.coaching.review import WindowResult
from round_review.video.probe import Recording

REPORT_FILENAME = "report.md"


@dataclass(frozen=True, slots=True)
class Report:
    recording: Recording
    generated_at: datetime
    model: str
    results: tuple[WindowResult, ...]
    warnings: tuple[str, ...]


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _relative(path: Path | None, base_dir: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _render_finding(index: int, finding: Finding, base_dir: Path) -> list[str]:
    lines = [
        f"### {_clock(finding.timestamp_s)} {finding.category}",
        "",
        finding.observation,
        "",
    ]
    evidence = _relative(finding.evidence_frame, base_dir)
    if evidence:
        lines += [f"![t={finding.timestamp_s:.1f}s]({evidence})", ""]
    lines += [
        f"**What you could see:** {finding.visible_evidence}",
        "",
        f"**What you knew:** {finding.information_available_to_player}",
        "",
    ]
    if finding.information_revealed_later.strip():
        lines += [f"**What you couldn't have known:** {finding.information_revealed_later}", ""]
    if finding.assumption_flags:
        lines += [f"**Assumptions:** {', '.join(finding.assumption_flags)}", ""]
    lines += [
        f"**Try instead:** {finding.suggested_alternative}",
        "",
        f"Confidence: {finding.confidence:.2f}",
        "",
    ]
    return lines


def _render_window(result: WindowResult, base_dir: Path) -> list[str]:
    w = result.window
    lines = [f"## Window {w.index + 1}: {_clock(w.start_s)} - {_clock(w.end_s)}", ""]
    if not result.findings:
        lines += ["No findings in this window.", ""]
    for i, finding in enumerate(result.findings, start=1):
        lines += _render_finding(i, finding, base_dir)
    if result.warnings:
        lines += ["Warnings:", *[f"- {msg}" for msg in result.warnings], ""]
    return lines


def render_report(report: Report, base_dir: Path) -> str:
    rec = report.recording
    lines = [
        f"# Review: {rec.path.name}",
        "",
        f"- Generated: {report.generated_at.isoformat(timespec='minutes')}",
        f"- Model: {report.model}",
        f"- Recording: {rec.width}x{rec.height} @ {rec.fps:.0f} fps, {_clock(rec.duration_s)}",
        f"- Windows reviewed: {len(report.results)}",
        "",
    ]
    for result in report.results:
        lines += _render_window(result, base_dir)
    if report.warnings:
        lines += ["## Warnings", "", *[f"- {msg}" for msg in report.warnings], ""]
    return "\n".join(lines)


def write_report(report: Report, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_FILENAME
    path.write_text(render_report(report, out_dir), encoding="utf-8")
    return path
