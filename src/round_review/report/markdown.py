"""Render a Report to Markdown. Evidence frames are linked relative to the report file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from round_review.coaching.knowledge import Checklist, load_checklist
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
    # Set when the review stopped early; the report covers less than it planned to.
    stopped_reason: str | None = None

    @property
    def partial(self) -> bool:
        return self.stopped_reason is not None


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


def check_label(checklist: Checklist, check_id: str) -> str | None:
    """'Category name / check text' for a known check id, else None."""
    for cat in checklist.categories:
        for check in cat.checks:
            if check.id == check_id:
                return f"{cat.name} / {check.check}"
    return None


def _render_finding(
    index: int, finding: Finding, base_dir: Path, checklist: Checklist
) -> list[str]:
    lines = [f"### {_clock(finding.timestamp_s)} {finding.category}", ""]
    label = check_label(checklist, finding.check_id)
    if label:
        lines += [f"Checklist: {label}", ""]
    lines += [finding.observation, ""]
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


def _context_line(result: WindowResult) -> str | None:
    ctx = result.context
    if not ctx.agent and not ctx.map:
        return None
    where = f" on {ctx.map}" if ctx.map else ""
    side = f" ({ctx.side})" if ctx.side else ""
    return f"{ctx.agent or 'Unknown agent'}{where}{side}"


def _render_window(result: WindowResult, base_dir: Path, checklist: Checklist) -> list[str]:
    w = result.window
    lines = [f"## Window {w.index + 1}: {_clock(w.start_s)} - {_clock(w.end_s)}", ""]
    context_line = _context_line(result)
    if context_line:
        lines += [context_line, ""]
    if result.situation:
        lines += [f"Situation: {result.situation.summary}", ""]
    if not result.findings:
        lines += ["No findings in this window.", ""]
    for i, finding in enumerate(result.findings, start=1):
        lines += _render_finding(i, finding, base_dir, checklist)
    if result.warnings:
        lines += ["Warnings:", *[f"- {msg}" for msg in result.warnings], ""]
    return lines


def render_report(report: Report, base_dir: Path, checklist: Checklist | None = None) -> str:
    checklist = checklist or load_checklist()
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
    if report.stopped_reason:
        lines += [f"> **Partial review.** {report.stopped_reason}", ""]
    for result in report.results:
        lines += _render_window(result, base_dir, checklist)
    if report.warnings:
        lines += ["## Warnings", "", *[f"- {msg}" for msg in report.warnings], ""]
    return "\n".join(lines)


def write_report(report: Report, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_FILENAME
    path.write_text(render_report(report, out_dir), encoding="utf-8")
    return path
