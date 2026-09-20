from pathlib import Path

import pytest

from round_review.config import Config, load_config
from round_review.errors import ConfigError


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml", env={}, data_dir=tmp_path)
    assert cfg.model == "qwen3-vl:8b"
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.window_s == 12.0
    assert cfg.windows_per_file == 3
    assert cfg.fps == 1.0
    assert cfg.frame_width == 1280
    assert cfg.daily_call_cap == 30
    assert cfg.poll_s == 20.0
    assert cfg.quiet_polls == 3
    assert cfg.min_age_s == 120.0
    assert cfg.request_timeout_s == 300.0
    assert cfg.ffmpeg_path == "ffmpeg"
    assert cfg.ffprobe_path == "ffprobe"
    assert cfg.recordings_dir is None
    assert cfg.reports_dir == tmp_path / "reports"
    assert cfg.ledger_path == tmp_path / "ledger.jsonl"


def test_toml_values_override_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "qwen3-vl:4b"\nrecordings_dir = "/vids"\nwindows_per_file = 5\nfps = 0.5\n'
    )
    cfg = load_config(path, env={}, data_dir=tmp_path)
    assert cfg.model == "qwen3-vl:4b"
    assert cfg.recordings_dir == Path("/vids")
    assert cfg.windows_per_file == 5
    assert cfg.fps == 0.5


def test_env_overrides_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "from-toml"\ndaily_call_cap = 5\n')
    env = {"ROUND_REVIEW_MODEL": "from-env", "ROUND_REVIEW_DAILY_CALL_CAP": "9"}
    cfg = load_config(path, env=env, data_dir=tmp_path)
    assert cfg.model == "from-env"
    assert cfg.daily_call_cap == 9


def test_unknown_key_is_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("bogus = 1\n")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(path, env={}, data_dir=tmp_path)


def test_bad_type_is_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('windows_per_file = "three"\n')
    with pytest.raises(ConfigError, match="windows_per_file"):
        load_config(path, env={}, data_dir=tmp_path)


def test_bad_env_value_is_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ROUND_REVIEW_FPS"):
        load_config(tmp_path / "x.toml", env={"ROUND_REVIEW_FPS": "fast"}, data_dir=tmp_path)


def test_non_positive_values_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("window_s = 0\n")
    with pytest.raises(ConfigError, match="window_s"):
        load_config(path, env={}, data_dir=tmp_path)


def test_invalid_toml_is_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("model = \n")
    with pytest.raises(ConfigError):
        load_config(path, env={}, data_dir=tmp_path)


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "x.toml", env={}, data_dir=tmp_path)
    assert isinstance(cfg, Config)
    with pytest.raises(AttributeError):
        cfg.model = "other"  # type: ignore[misc]
