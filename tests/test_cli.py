from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from round_review import cli
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
    result = CliRunner().invoke(cli.main, ["review", str(video), "--context", "Gold 2", "--force"])
    assert result.exit_code == 0, result.output
    assert patched["review"] == (video, "Gold 2", True)
    assert "clip.mp4" in result.output


def test_review_missing_file_exits_2(patched: dict[str, object], tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.main, ["review", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 2


def test_review_error_exits_1_with_class_name(
    monkeypatch: pytest.MonkeyPatch, patched: dict[str, object], tmp_path: Path
) -> None:
    def boom(path: Path, deps: Deps, context: str | None = None, force: bool = False) -> Report:
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
