"""Configuration: TOML file in the user data dir, overridden by ROUND_REVIEW_* env vars."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from round_review.errors import ConfigError, RoundReviewError

APP_NAME = "round-review"
ENV_PREFIX = "ROUND_REVIEW_"
DEFAULT_NUM_CTX = 16384


@dataclass(frozen=True, slots=True)
class Config:
    reports_dir: Path
    ledger_path: Path
    recordings_dir: Path | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3-vl:8b"
    num_ctx: int = DEFAULT_NUM_CTX
    window_s: float = 12.0
    # "full" tiles the whole recording; "sampled" takes `windows_per_file` spread evenly.
    coverage: str = "full"
    windows_per_file: int = 3
    # 0 = unlimited. `max_span_s` reviews only the first N seconds of gameplay.
    max_windows: int = 0
    max_span_s: float = 0.0
    edge_skip_s: float = 30.0
    fps: float = 1.0
    frame_width: int = 1280
    daily_call_cap: int = 0
    poll_s: float = 20.0
    quiet_polls: int = 3
    min_age_s: float = 120.0
    # One coach call carries every frame of a window, which on a larger model can run for
    # many minutes. Too short a timeout throws away a window that was nearly finished.
    request_timeout_s: float = 900.0
    api_port: int = 8765
    # Two model calls per window (situation read, then coaching). Off = coaching only.
    situation_pass: bool = True
    # How many frames the situation pass carries. It runs for every window, including the
    # ones it then skips, so its images dominate the cost of a review. 0 = every frame.
    situation_frames: int = 3
    # Frames the coach pass carries. 0 = every extracted frame, which is the most detail and
    # the slowest call; lower it if calls are timing out.
    coach_frames: int = 0
    # Frames a "what should I have done here" question carries, and the longest stretch of
    # footage one question may cover.
    question_frames: int = 6
    max_question_span_s: float = 60.0
    # Keep the model loaded between calls; a review is dozens of calls back to back.
    ollama_keep_alive: str = "30m"
    # Give up after this many windows in a row are skipped before coaching. A model that
    # cannot read the scene skips everything, and finding that out should take minutes,
    # not hours. 0 disables it.
    abstain_streak_limit: int = 20
    player_notes: str = ""
    # Deterministic HUD reading. The clock is the one fact a vision model cannot argue with,
    # so it vetoes phase misreads. The region is x,y,w,h as fractions of the frame; check it
    # against your own footage with `round-review hud crop`.
    hud_check: bool = True
    hud_timer_region: str = "0.455,0.020,0.090,0.055"
    hud_templates_path: Path | None = None
    hud_min_confidence: float = 0.8
    # -1 = adaptive; a calibrated fixed cutoff separates white text from bright scenery.
    hud_threshold: int = -1
    buy_phase_max_s: float = 45.0


# Keys whose values must be strictly positive. Everything else is a path or string.
POSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "window_s",
        "windows_per_file",
        "fps",
        "frame_width",
        "poll_s",
        "quiet_polls",
        "min_age_s",
        "request_timeout_s",
        "api_port",
        "num_ctx",
        "buy_phase_max_s",
        "max_question_span_s",
    }
)
# 0 is allowed and means "unlimited"; negative never is.
NON_NEGATIVE_KEYS: frozenset[str] = frozenset(
    {
        "daily_call_cap",
        "max_windows",
        "max_span_s",
        "situation_frames",
        "abstain_streak_limit",
        "coach_frames",
    }
)
PATH_KEYS: frozenset[str] = frozenset(
    {"recordings_dir", "reports_dir", "ledger_path", "hud_templates_path"}
)
COVERAGE_MODES: frozenset[str] = frozenset({"full", "sampled"})


def default_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False))


def default_config_path(data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / "config.toml"


def _field_types() -> dict[str, Any]:
    return {f.name: f.type for f in fields(Config)}


def _coerce(key: str, raw: object, source: str) -> object:
    """Coerce a TOML or env value to the field's declared type, or raise ConfigError."""
    declared = _field_types()[key]
    try:
        if key in PATH_KEYS:
            if not isinstance(raw, str):
                raise TypeError("expected a path string")
            return Path(raw).expanduser()
        if declared in ("bool", bool):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in ("true", "1", "yes", "on"):
                return True
            if isinstance(raw, str) and raw.strip().lower() in ("false", "0", "no", "off"):
                return False
            raise TypeError("expected a boolean")
        if declared in ("int", int):
            if isinstance(raw, bool) or not isinstance(raw, int | str):
                raise TypeError("expected an integer")
            return int(raw)
        if declared in ("float", float):
            if isinstance(raw, bool) or not isinstance(raw, int | float | str):
                raise TypeError("expected a number")
            return float(raw)
        if declared in ("str", str):
            if not isinstance(raw, str):
                raise TypeError("expected a string")
            return raw
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{source}: invalid value for {key}: {exc}") from exc
    raise ConfigError(f"{source}: unsupported config field {key}")


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: cannot read config: {exc}") from exc


def _env_overrides(env: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """Return {field: (raw_value, env_var_name)} for every ROUND_REVIEW_* var."""
    known = _field_types()
    out: dict[str, tuple[str, str]] = {}
    for name, value in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        key = name.removeprefix(ENV_PREFIX).lower()
        if key not in known:
            raise ConfigError(f"unknown environment override {name}")
        out[key] = (value, name)
    return out


def load_config(
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> Config:
    """Build a Config from defaults, then the TOML file at `path`, then env overrides."""
    data_dir = data_dir or default_data_dir()
    path = path or default_config_path(data_dir)
    env = {} if env is None else env
    known = _field_types()

    values: dict[str, object] = {
        "reports_dir": data_dir / "reports",
        "ledger_path": data_dir / "ledger.jsonl",
        "hud_templates_path": data_dir / "hud-digits.json",
    }

    for key, raw in _read_toml(path).items():
        if key not in known:
            raise ConfigError(f"{path}: unknown config key {key}")
        values[key] = _coerce(key, raw, str(path))

    for key, (raw, var) in _env_overrides(env).items():
        values[key] = _coerce(key, raw, var)

    for key in POSITIVE_KEYS:
        if key in values and not isinstance(values[key], Path):
            number = values[key]
            assert isinstance(number, int | float)
            if number <= 0:
                raise ConfigError(f"{key} must be > 0, got {number}")

    for key in NON_NEGATIVE_KEYS:
        if key in values:
            number = values[key]
            assert isinstance(number, int | float)
            if number < 0:
                raise ConfigError(f"{key} must be >= 0 (0 means unlimited), got {number}")

    threshold = values.get("hud_threshold", -1)
    if not isinstance(threshold, int) or not -1 <= threshold <= 255:
        raise ConfigError("hud_threshold must be -1 (adaptive) or an integer from 0 to 255")

    region = values.get("hud_timer_region")
    if isinstance(region, str):
        from round_review.vision.hud import parse_region

        try:
            parse_region(region)
        except RoundReviewError as exc:
            raise ConfigError(f"hud_timer_region: {exc}") from exc

    coverage = values.get("coverage")
    if coverage is not None and coverage not in COVERAGE_MODES:
        raise ConfigError(f"coverage must be one of {sorted(COVERAGE_MODES)}, got {coverage!r}")

    return Config(**values)  # type: ignore[arg-type]
