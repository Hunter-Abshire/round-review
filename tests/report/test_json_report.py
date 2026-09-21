import json
from pathlib import Path

from round_review.report.json_report import load_report_json, report_to_dict, write_report_json
from tests.report.test_markdown import build_report


def test_report_to_dict_is_json_serialisable_with_relative_frames(tmp_path: Path) -> None:
    data = report_to_dict(build_report(tmp_path), base_dir=tmp_path)
    json.dumps(data)
    assert data["recording"]["name"] == "match.mp4"
    assert data["recording"]["duration_s"] == 600.0
    assert data["model"] == "qwen3-vl:8b"
    assert data["generated_at"].startswith("2026-09-20")
    (window,) = data["windows"]
    assert window["index"] == 0 and window["start_s"] == 60.0 and window["end_s"] == 72.0
    (finding,) = window["findings"]
    assert finding["timestamp_s"] == 64.4
    assert finding["evidence_frame"] == "frames/w00_004.jpg"
    assert finding["check_id"] == "crosshair.head_level"
    assert finding["check_label"] is not None
    assert finding["check_label"].startswith("Crosshair placement")
    assert " / " in finding["check_label"]
    assert window["situation"]["agent"] == "Jett"
    assert window["situation"]["summary"] == "Entering A main with dash up."
    assert window["context"] == {
        "rank": "Gold 2",
        "agent": "Jett",
        "map": "Ascent",
        "side": "attack",
        "focus": None,
        "notes": None,
    }
    assert finding["assumption_flags"] == ["enemy position", "flash cooldown"]
    assert window["warnings"] == ["window warning"]
    assert data["warnings"] == ["file warning"]


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "out"
    path = write_report_json(build_report(out), out)
    assert path == out / "report.json"
    assert load_report_json(path) == report_to_dict(build_report(out), base_dir=out)


def test_partial_and_abstention_reach_the_json(tmp_path: Path) -> None:
    from dataclasses import replace

    base = build_report(tmp_path)
    partial = replace(base, stopped_reason="CapExceeded: daily model-call cap reached (30/30)")
    data = report_to_dict(partial, base_dir=tmp_path)
    assert data["partial"] is True
    assert "cap" in data["stopped_reason"]
    assert data["windows"][0]["abstained_reason"] is None
    assert report_to_dict(base, base_dir=tmp_path)["partial"] is False
