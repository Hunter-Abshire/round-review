import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from round_review import cli
from round_review.coaching.context import PlayerContext
from round_review.config import Config
from round_review.errors import OllamaError
from round_review.ledger import LedgerEntry, append_entry
from round_review.pipeline import Deps
from round_review.report.markdown import Report
from round_review.video.probe import Recording


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        reports_dir=tmp_path / "reports",
        ledger_path=tmp_path / "ledger.jsonl",
        recordings_dir=tmp_path / "vids",
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> dict[str, object]:
    calls: dict[str, object] = {}
    monkeypatch.setattr(cli, "load_config", lambda path, env, data_dir=None: cfg)

    def fake_review(
        path: Path, deps: Deps, context: str | None = None, force: bool = False
    ) -> Report:
        calls["review"] = (path, context, force)
        rec = Recording(path, 1.0, 30.0, 1, 1, 1, 0.0)
        return Report(rec, datetime(2026, 9, 20, tzinfo=UTC), cfg.model, (), ())

    monkeypatch.setattr(cli, "review_file", fake_review)
    monkeypatch.setattr(
        cli, "make_default_deps", lambda c: Deps(c, None, None, None, lambda: datetime.now(UTC))
    )  # type: ignore[arg-type]
    return calls


def test_review_happy_path(patched: dict[str, object], tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    result = CliRunner().invoke(
        cli.main,
        [
            "review",
            str(video),
            "--rank",
            "Gold 2",
            "--agent",
            "Jett",
            "--map",
            "Ascent",
            "--side",
            "attack",
            "--focus",
            "entries",
            "--context",
            "notes",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output
    assert patched["review"] == (
        video,
        PlayerContext(
            rank="Gold 2", agent="Jett", map="Ascent", side="attack", focus="entries", notes="notes"
        ),
        True,
    )
    assert "clip.mp4" in result.output


def test_review_missing_file_exits_2(patched: dict[str, object], tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.main, ["review", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 2


def test_review_error_exits_1_with_class_name(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    def boom(
        path: Path, deps: Deps, context: PlayerContext | None = None, force: bool = False
    ) -> Report:
        raise OllamaError("connection refused")

    monkeypatch.setattr(cli, "review_file", boom)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    result = CliRunner().invoke(cli.main, ["review", str(video)])
    assert result.exit_code == 1
    assert "OllamaError: connection refused" in result.output


def test_config_show(patched: dict[str, object], cfg: Config) -> None:
    result = CliRunner().invoke(cli.main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "model = qwen3-vl:8b" in result.output
    assert str(cfg.ledger_path) in result.output


def test_ledger_list(patched: dict[str, object], cfg: Config) -> None:
    append_entry(
        cfg.ledger_path,
        LedgerEntry("k1", "/v/a.mp4", datetime(2026, 9, 20, tzinfo=UTC), 3, "/r/a.md", "ok", None),
    )
    result = CliRunner().invoke(cli.main, ["ledger", "list"])
    assert result.exit_code == 0, result.output
    assert "a.mp4" in result.output and "ok" in result.output and "3" in result.output


def test_watch_requires_recordings_dir(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    bare = Config(reports_dir=tmp_path, ledger_path=tmp_path / "l.jsonl")
    monkeypatch.setattr(cli, "load_config", lambda path, env, data_dir=None: bare)
    result = CliRunner().invoke(cli.main, ["watch"])
    assert result.exit_code == 1
    assert "recordings_dir" in result.output


def test_watch_invokes_loop_with_directory(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def fake_loop(**kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(cli, "watch_loop", fake_loop)
    d = tmp_path / "custom"
    d.mkdir()
    result = CliRunner().invoke(cli.main, ["watch", str(d)])
    assert result.exit_code == 0, result.output
    assert seen["directory"] == d


def test_serve_binds_loopback_and_uses_config_port(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], cfg: Config
) -> None:
    seen: dict[str, object] = {}

    def fake_run(app: object, host: str, port: int, log_level: str) -> None:
        seen.update(app=app, host=host, port=port)

    monkeypatch.setattr(cli, "uvicorn_run", fake_run)
    result = CliRunner().invoke(cli.main, ["serve"])
    assert result.exit_code == 0, result.output
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == cfg.api_port
    result = CliRunner().invoke(cli.main, ["serve", "--port", "9999"])
    assert result.exit_code == 0 and seen["port"] == 9999


@pytest.fixture
def scene_video(tmp_path: Path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 64)
    return video


def test_review_accepts_coverage_overrides(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], cfg: Config, scene_video: Path
) -> None:
    seen: dict[str, object] = {}

    def fake_review(
        path: Path,
        deps: Deps,
        context: PlayerContext | None = None,
        force: bool = False,
        **kw: object,
    ) -> Report:
        seen["coverage"] = deps.config.coverage
        seen["max_span_s"] = deps.config.max_span_s
        seen["max_windows"] = deps.config.max_windows
        rec = Recording(path, 1.0, 30.0, 1, 1, 1, 0.0)
        return Report(rec, datetime(2026, 9, 21, tzinfo=UTC), cfg.model, (), ())

    monkeypatch.setattr(cli, "review_file", fake_review)
    result = CliRunner().invoke(
        cli.main,
        [
            "review",
            str(scene_video),
            "--coverage",
            "sampled",
            "--first",
            "60",
            "--max-windows",
            "8",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {"coverage": "sampled", "max_span_s": 60.0, "max_windows": 8}


def test_scenes_scaffold_writes_a_labels_file(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    from round_review.validation.scenes import SceneCase

    calls: dict[str, object] = {}

    def fake_scaffold(
        recording: object, every_s: float, ffmpeg: object, width: int, frames_dir: Path
    ) -> list[SceneCase]:
        calls["every_s"] = every_s
        calls["frames_dir"] = frames_dir
        return [SceneCase(scene_video, 0.0, "", notes="look at f.jpg")]

    monkeypatch.setattr(
        cli, "probe", lambda path, runner: Recording(path, 300.0, 60.0, 1, 1, 1, 0.0)
    )
    monkeypatch.setattr(cli, "scaffold_cases", fake_scaffold)
    out = tmp_path / "labels.json"
    result = CliRunner().invoke(
        cli.main, ["scenes", "scaffold", str(scene_video), "--every", "45", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert calls["every_s"] == 45.0
    assert out.exists()
    assert "expected_phase" in out.read_text()
    assert "1 frame" in result.output or "1 case" in result.output


def test_scenes_validate_prints_the_report_and_writes_json(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    from round_review.validation.scenes import SceneCase, SceneOutcome, SceneReport

    case = SceneCase(scene_video, 10.0, "early")
    report = SceneReport(
        "qwen3-vl:8b", 1, (SceneOutcome(case, None, "OllamaError: refused", None),)
    )
    monkeypatch.setattr(cli, "load_cases", lambda path: [case])
    monkeypatch.setattr(cli, "run_cases", lambda *a, **k: report)
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    out_json = tmp_path / "scenes.json"
    result = CliRunner().invoke(
        cli.main, ["scenes", "validate", str(labels), "--json-out", str(out_json)]
    )
    assert result.exit_code == 0, result.output
    assert "Scene recognition" in result.output
    assert json.loads(out_json.read_text())["model"] == "qwen3-vl:8b"


def test_scenes_validate_can_gate_on_accuracy(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    from round_review.validation.scenes import SceneCase, SceneOutcome, SceneReport

    case = SceneCase(scene_video, 10.0, "early")
    report = SceneReport("m", 1, (SceneOutcome(case, None, "unreadable", None),))
    monkeypatch.setattr(cli, "load_cases", lambda path: [case])
    monkeypatch.setattr(cli, "run_cases", lambda *a, **k: report)
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    result = CliRunner().invoke(
        cli.main, ["scenes", "validate", str(labels), "--min-accuracy", "0.8"]
    )
    assert result.exit_code == 1
    assert "below" in result.output


def test_scenes_validate_reports_bad_labels(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    from round_review.errors import LabelsError

    def boom(path: Path) -> list[object]:
        raise LabelsError("no labelled cases")

    monkeypatch.setattr(cli, "load_cases", boom)
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    result = CliRunner().invoke(cli.main, ["scenes", "validate", str(labels)])
    assert result.exit_code == 1
    assert "LabelsError: no labelled cases" in result.output


def test_scenes_describe_prints_the_situation_json(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path
) -> None:
    from round_review.coaching.situation import Situation
    from round_review.validation.scenes import SceneCase, SceneOutcome, SceneReport

    situation = Situation(
        "Jett", "Ascent", "attack", "early", "Vandal", (), None, None, 0, (), "Live round."
    )
    report = SceneReport(
        "m",
        1,
        (SceneOutcome(SceneCase(scene_video, 12.0, "early"), situation, None, Path("/f/x.jpg")),),
    )
    monkeypatch.setattr(
        cli, "probe", lambda path, runner: Recording(path, 300.0, 60.0, 1, 1, 1, 0.0)
    )
    monkeypatch.setattr(cli, "run_cases", lambda *a, **k: report)
    result = CliRunner().invoke(cli.main, ["scenes", "describe", str(scene_video), "--at", "12"])
    assert result.exit_code == 0, result.output
    assert "early" in result.output
    assert "Live round." in result.output
    assert str(Path("/f/x.jpg")) in result.output


def test_hud_crop_saves_a_picture_to_check_the_region(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    saved: dict[str, object] = {}

    def fake_run(args: list[str]) -> str:
        saved["args"] = args
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"png")
        return ""

    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1920, 1080, 1, 0.0))
    monkeypatch.setattr(
        cli, "SubprocessRunner", lambda exe: type("R", (), {"run": staticmethod(fake_run)})()
    )
    result = CliRunner().invoke(
        cli.main, ["hud", "crop", str(scene_video), "--at", "45", "--out", str(tmp_path / "t.png")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "t.png").exists()
    assert "t.png" in result.output
    assert "crop=" in " ".join(saved["args"])  # type: ignore[arg-type]


def test_hud_learn_teaches_digits_and_reports_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    from round_review.vision.raster import Glyph

    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1920, 1080, 1, 0.0))
    monkeypatch.setattr(
        cli,
        "learn_from_crop",
        lambda *a, **k: [("1", Glyph(("#",), 0.5)), (":", Glyph((".",), 0.2))],
    )
    store = tmp_path / "digits.json"
    result = CliRunner().invoke(
        cli.main,
        ["hud", "learn", str(scene_video), "--at", "45", "--reads", "1:", "--store", str(store)],
    )
    assert result.exit_code == 0, result.output
    assert store.exists()
    assert "Still missing" in result.output
    assert "0" in result.output and "9" in result.output


def test_hud_learning_uses_calibrated_threshold(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, scene_video: Path, tmp_path: Path
) -> None:
    from dataclasses import replace

    from round_review.vision.raster import Glyph

    config = replace(cfg, hud_threshold=220)
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1280, 720, 1, 0.0))
    seen = []

    def learn(*a, **kw):
        seen.append(kw["threshold"])
        return [("1", Glyph(("#",), 0.5))]

    monkeypatch.setattr(cli, "learn_from_crop", learn)
    result = CliRunner().invoke(
        cli.main,
        [
            "hud",
            "learn",
            str(scene_video),
            "--at",
            "45",
            "--reads",
            "1",
            "--store",
            str(tmp_path / "digits.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == [220]


def test_hud_learn_reports_a_mismatched_reading(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path, tmp_path: Path
) -> None:
    from round_review.errors import HudError

    def boom(*a: object, **k: object) -> list[object]:
        raise HudError("the crop has 4 glyph(s) but you said it reads '12:34'")

    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1920, 1080, 1, 0.0))
    monkeypatch.setattr(cli, "learn_from_crop", boom)
    result = CliRunner().invoke(
        cli.main, ["hud", "learn", str(scene_video), "--at", "45", "--reads", "12:34"]
    )
    assert result.exit_code == 1
    assert "HudError" in result.output


def test_hud_read_prints_the_clock_and_what_it_proves(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path
) -> None:
    from round_review.vision.hud import HudRead

    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1920, 1080, 1, 0.0))
    monkeypatch.setattr(cli, "read_hud", lambda *a, **k: HudRead("1:39", 99.0, 0.94, 4))
    result = CliRunner().invoke(cli.main, ["hud", "read", str(scene_video), "--at", "45"])
    assert result.exit_code == 0, result.output
    assert "1:39" in result.output
    assert "live" in result.output.lower()


def test_hud_read_says_when_it_cannot_read_the_clock(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], scene_video: Path
) -> None:
    from round_review.vision.hud import HudRead

    monkeypatch.setattr(cli, "probe", lambda p, r: Recording(p, 300.0, 60.0, 1920, 1080, 1, 0.0))
    monkeypatch.setattr(
        cli, "read_hud", lambda *a, **k: HudRead(None, None, 0.0, 7, Path("/tmp/c.pgm"))
    )
    result = CliRunner().invoke(cli.main, ["hud", "read", str(scene_video), "--at", "45"])
    assert result.exit_code == 0
    assert "7 glyph" in result.output
    assert "hud learn" in result.output  # tells you what to do about it


def test_config_path_prints_the_file_location(patched: dict[str, object], tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.main, ["config", "path"])
    assert result.exit_code == 0, result.output
    assert "config.toml" in result.output


def test_config_init_writes_a_starter_file(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    target = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "default_config_path", lambda: target)
    result = CliRunner().invoke(cli.main, ["config", "init"])
    assert result.exit_code == 0, result.output
    text = target.read_text()
    assert "recordings_dir" in text
    assert "model" in text
    assert "hud_check" in text
    assert str(target) in result.output
    # the starter file must be valid and loadable
    from round_review.config import load_config

    load_config(target, env={}, data_dir=tmp_path)


def test_config_init_refuses_to_clobber(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    target = tmp_path / "config.toml"
    target.write_text('model = "mine"\n')
    monkeypatch.setattr(cli, "default_config_path", lambda: target)
    result = CliRunner().invoke(cli.main, ["config", "init"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert target.read_text() == 'model = "mine"\n'
    forced = CliRunner().invoke(cli.main, ["config", "init", "--force"])
    assert forced.exit_code == 0
    assert "recordings_dir" in target.read_text()


def test_config_init_can_set_the_recordings_folder(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    target = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "default_config_path", lambda: target)
    vids = tmp_path / "vids"
    vids.mkdir()
    result = CliRunner().invoke(cli.main, ["config", "init", "--recordings-dir", str(vids)])
    assert result.exit_code == 0, result.output
    assert f'recordings_dir = "{vids.as_posix()}"' in target.read_text()
