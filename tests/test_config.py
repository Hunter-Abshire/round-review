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
    assert cfg.daily_call_cap == 0
    assert cfg.situation_pass is True
    assert cfg.poll_s == 20.0
    assert cfg.quiet_polls == 3
    assert cfg.min_age_s == 120.0
    assert cfg.request_timeout_s == 900.0
    assert cfg.num_ctx == 16384
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


def test_context_size_from_toml_and_environment(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("num_ctx = 32768\n")
    assert load_config(path, env={}, data_dir=tmp_path).num_ctx == 32768
    cfg = load_config(path, env={"ROUND_REVIEW_NUM_CTX": "65536"}, data_dir=tmp_path)
    assert cfg.num_ctx == 65536


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"large"'])
def test_invalid_context_size_rejected(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"num_ctx = {value}\n")
    with pytest.raises(ConfigError, match="num_ctx"):
        load_config(path, env={}, data_dir=tmp_path)


def test_bool_from_toml_and_env(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("situation_pass = false\n")
    assert load_config(path, env={}, data_dir=tmp_path).situation_pass is False
    assert (
        load_config(
            path, env={"ROUND_REVIEW_SITUATION_PASS": "true"}, data_dir=tmp_path
        ).situation_pass
        is True
    )
    assert (
        load_config(
            path, env={"ROUND_REVIEW_SITUATION_PASS": "0"}, data_dir=tmp_path
        ).situation_pass
        is False
    )
    with pytest.raises(ConfigError, match="SITUATION_PASS"):
        load_config(path, env={"ROUND_REVIEW_SITUATION_PASS": "maybe"}, data_dir=tmp_path)


def test_coverage_defaults_to_full_video(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "x.toml", env={}, data_dir=tmp_path)
    assert cfg.coverage == "full"
    assert cfg.max_windows == 0  # unlimited
    assert cfg.max_span_s == 0.0  # whole recording
    assert cfg.daily_call_cap == 0  # unlimited: local inference has no per-call cost


def test_coverage_can_be_limited(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coverage = "sampled"\nmax_span_s = 60.0\nmax_windows = 10\n')
    cfg = load_config(path, env={}, data_dir=tmp_path)
    assert (cfg.coverage, cfg.max_span_s, cfg.max_windows) == ("sampled", 60.0, 10)


def test_unknown_coverage_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coverage = "everything"\n')
    with pytest.raises(ConfigError, match="coverage"):
        load_config(path, env={}, data_dir=tmp_path)


def test_caps_may_be_zero_but_not_negative(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("daily_call_cap = 0\nmax_windows = 0\nmax_span_s = 0\n")
    assert load_config(path, env={}, data_dir=tmp_path).daily_call_cap == 0
    path.write_text("max_windows = -1\n")
    with pytest.raises(ConfigError, match="max_windows"):
        load_config(path, env={}, data_dir=tmp_path)


@pytest.mark.parametrize("threshold", [-1, 0, 220, 255])
def test_hud_threshold_can_be_calibrated(tmp_path: Path, threshold: int) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"hud_threshold = {threshold}\n")
    assert load_config(path, env={}, data_dir=tmp_path).hud_threshold == threshold


@pytest.mark.parametrize("threshold", [-2, 256])
def test_invalid_hud_threshold_is_rejected(tmp_path: Path, threshold: int) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"hud_threshold = {threshold}\n")
    with pytest.raises(ConfigError, match="hud_threshold"):
        load_config(path, env={}, data_dir=tmp_path)


def test_request_timeout_allows_for_a_slow_coach_call(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "x.toml", env={}, data_dir=tmp_path)
    # a coach call on an 8b model with a dozen frames can run for many minutes
    assert cfg.request_timeout_s == 900.0
    assert cfg.coach_frames == 0  # 0 = every extracted frame
