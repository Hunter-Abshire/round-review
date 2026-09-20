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
    assert finding["assumption_flags"] == ["enemy position", "flash cooldown"]
    assert window["warnings"] == ["window warning"]
    assert data["warnings"] == ["file warning"]


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "out"
    path = write_report_json(build_report(out), out)
    assert path == out / "report.json"
    assert load_report_json(path) == report_to_dict(build_report(out), base_dir=out)
