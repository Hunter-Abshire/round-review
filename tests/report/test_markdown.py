from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.context import PlayerContext
from round_review.coaching.parse import Finding
from round_review.coaching.review import WindowResult
from round_review.coaching.situation import Situation
from round_review.report.markdown import Report, render_report, write_report
from round_review.video.frames import FrameSample
from round_review.video.probe import Recording
from round_review.video.windows import Window


def build_report(out_dir: Path, findings: bool = True) -> Report:
    rec = Recording(Path("/vids/match.mp4"), 600.0, 60.0, 1920, 1080, 1234, 0.0)
    window = Window(0, 60.0, 72.0, "evenly_spaced")
    frame = out_dir / "frames" / "w00_004.jpg"
    samples = (FrameSample(0, 60.0, out_dir / "frames" / "w00_000.jpg"),)
    finding = Finding(
        timestamp_s=64.4,
        check_id="crosshair.head_level",
        category="positioning",
        observation="Held a wide angle.",
        visible_evidence="Minimap at t=64.0s shows no teammate right.",
        information_available_to_player="Minimap, timer 1:05.",
        information_revealed_later="Enemy was behind the box.",
        assumption_flags=("enemy position", "flash cooldown"),
        suggested_alternative="Tighten the angle.",
        confidence=0.5,
        evidence_frame=frame,
    )
    situation = Situation(
        "Jett",
        "Ascent",
        "attack",
        "early",
        "Vandal",
        ("Tailwind",),
        3900,
        4,
        0,
        ((60.0, "walking A main"),),
        "Entering A main with dash up.",
    )
    result = WindowResult(
        window,
        samples,
        (finding,) if findings else (),
        1,
        ("window warning",),
        situation,
        PlayerContext(rank="Gold 2", agent="Jett", map="Ascent", side="attack"),
    )
    return Report(
        recording=rec,
        generated_at=datetime(2026, 9, 20, 15, 30, tzinfo=UTC),
        model="qwen3-vl:8b",
        results=(result,),
        warnings=("file warning",),
    )


def test_render_contains_all_sections(tmp_path: Path) -> None:
    text = render_report(build_report(tmp_path), base_dir=tmp_path)
    assert text.startswith("# Review: match.mp4")
    assert "qwen3-vl:8b" in text
    assert "2026-09-20" in text
    assert "## Window 1: 1:00 - 1:12" in text
    assert "### 1:04 positioning" in text
    assert "Checklist: Crosshair placement" in text
    assert "Situation: Entering A main with dash up." in text
    assert "Jett on Ascent (attack)" in text
    assert "**What you could see:** Minimap at t=64.0s shows no teammate right." in text
    assert "**What you knew:** Minimap, timer 1:05." in text
    assert "**What you couldn't have known:** Enemy was behind the box." in text
    assert "**Assumptions:** enemy position, flash cooldown" in text
    assert "**Try instead:** Tighten the angle." in text
    assert "Confidence: 0.50" in text
    assert "![t=64.4s](frames/w00_004.jpg)" in text
    assert "- window warning" in text
    assert "- file warning" in text


def test_render_no_findings(tmp_path: Path) -> None:
    text = render_report(build_report(tmp_path, findings=False), base_dir=tmp_path)
    assert "No findings in this window." in text


def test_write_report_creates_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    path = write_report(build_report(out), out)
    assert path == out / "report.md"
    assert path.read_text(encoding="utf-8").startswith("# Review: match.mp4")
