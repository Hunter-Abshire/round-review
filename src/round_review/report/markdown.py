"""Render a Report to Markdown. Evidence frames are linked relative to the report file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from round_review.coaching.knowledge import Checklist, load_checklist
from round_review.coaching.parse import Finding, Strength
from round_review.coaching.review import WindowResult
from round_review.coaching.session import Habit, SessionSummary, first_sentence
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
    # The coach's part: the action set, the praise, and one thing to practise.
    summary: SessionSummary | None = None

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


# Said in the player's terms rather than as a tag: "you have been doing this for weeks" is
# the sentence that makes a habit feel like a habit.
TREND_SUFFIX: dict[str | None, str] = {
    "new": ", new this match",
    "repeat": ", seen before",
    "improving": ", improving",
    "persistent": ", every recent match",
}
TREND_NOTE: dict[str, str] = {
    "repeat": "This came up in a recent match too.",
    "improving": "This is happening less often than it used to. Keep going.",
    "persistent": "This has come up in every one of your recent matches. It is the habit, "
    "not the match.",
}


def _render_habit(index: int, habit: Habit, base_dir: Path) -> list[str]:
    """One focus item: what happened, where to look, and what to do instead."""
    times = "once" if habit.count == 1 else f"{habit.count} times"
    lines = [
        f"### {index}. {first_sentence(habit.lead.observation)}",
        "",
        f"*{habit.category}, {times}{TREND_SUFFIX.get(habit.trend, '')}*",
        "",
    ]
    if habit.count > 1:
        lines += [f"The same habit showed up {times} in this recording.", ""]
    if habit.trend and habit.trend != "new":
        lines += [TREND_NOTE[habit.trend], ""]
    moments = ", ".join(_clock(f.timestamp_s) for f in habit.instances[:5])
    lines += [f"**Where to look:** {moments}", ""]
    lines += [f"**What you could see:** {habit.lead.visible_evidence}", ""]
    if habit.lead.assumption_flags:
        lines += [f"**Assumed, not seen:** {', '.join(habit.lead.assumption_flags)}", ""]
    lines += [f"**Try instead:** {habit.lead.suggested_alternative}", ""]
    evidence = _relative(habit.lead.evidence_frame, base_dir)
    if evidence:
        lines += [f"![{_clock(habit.lead.timestamp_s)}]({evidence})", ""]
    return lines


def _render_strength(strength: Strength, base_dir: Path) -> list[str]:
    lines = [
        f"- **{_clock(strength.timestamp_s)}, {strength.category}.** {strength.observation}",
        f"  Why it worked: {strength.why_it_worked}",
    ]
    return lines


def _render_summary(summary: SessionSummary, base_dir: Path) -> list[str]:
    lines = ["## The one thing to fix", "", summary.verdict, ""]
    if summary.rank_focus:
        lines += [f"**At your rank:** {summary.rank_focus}", ""]

    if summary.focus:
        lines += [
            "## What to work on",
            "",
            "Capped at three on purpose. More than that and none of it gets acted on.",
            "",
        ]
        for i, habit in enumerate(summary.focus, start=1):
            lines += _render_habit(i, habit, base_dir)

    # Praise comes after the corrections: positive-first reads as a cushion and gets discounted.
    if summary.strengths:
        lines += ["## What worked", "", "Keep doing these.", ""]
        for strength in summary.strengths:
            lines += _render_strength(strength, base_dir)
        lines.append("")

    if summary.hindsight:
        lines += [
            "## Judged with hindsight, so treat with care",
            "",
            "These lean on something you could not have known at the time. They are here "
            "because the habit may still be worth a look, not because the decision was wrong.",
            "",
        ]
        for habit in summary.hindsight:
            lines.append(
                f"- **{_clock(habit.lead.timestamp_s)}, {habit.category}.** "
                f"{habit.lead.observation.rstrip('.')}. Only clear afterwards: "
                f"{habit.lead.information_revealed_later}"
            )
        lines.append("")

    if summary.also_seen:
        lines += ["## Everything else seen", "", "Not worth acting on this week.", ""]
        for habit in summary.also_seen:
            times = "once" if habit.count == 1 else f"{habit.count} times"
            lines.append(
                f"- **{habit.category}.** {first_sentence(habit.lead.observation)} ({times})"
            )
        lines.append("")

    if summary.practice:
        lines += [
            "## Before your next game",
            "",
            f"**In-game rule.** {summary.practice.rule}",
            "",
            f"**Drill.** {summary.practice.drill}",
            "",
            f"**And how you will know it worked.** {summary.practice.success_check}",
            "",
        ]
    return lines


def _render_observability(report: Report) -> list[str]:
    reviewed = sum(r.window.end_s - r.window.start_s for r in report.results)
    duration = report.recording.duration_s
    percent = round(100 * reviewed / duration) if duration else 0
    return [
        "## What this review could not see",
        "",
        f"Judged from frames sampled across {_clock(reviewed)} of {_clock(duration)} "
        f"({percent}%) of the recording, so anything between sampled frames is unseen. "
        "There is no per-round win or loss data, so how much each habit actually cost in "
        "rounds is estimated from the kind of mistake, not measured. Nothing here reads "
        "voice comms or your intent.",
        "",
    ]


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
    if report.summary:
        lines += _render_summary(report.summary, base_dir)
        lines += _render_observability(report)
    else:
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
