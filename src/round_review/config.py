"""Configuration: TOML file in the user data dir, overridden by ROUND_REVIEW_* env vars."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from round_review.errors import ConfigError

APP_NAME = "round-review"
ENV_PREFIX = "ROUND_REVIEW_"


@dataclass(frozen=True, slots=True)
class Config:
    reports_dir: Path
    ledger_path: Path
    recordings_dir: Path | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3-vl:8b"
    window_s: float = 12.0
    windows_per_file: int = 3
    edge_skip_s: float = 30.0
    fps: float = 1.0
    frame_width: int = 1280
    daily_call_cap: int = 30
    poll_s: float = 20.0
    quiet_polls: int = 3
    min_age_s: float = 120.0
    request_timeout_s: float = 300.0
    api_port: int = 8765


# Keys whose values must be strictly positive. Everything else is a path or string.
POSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "window_s",
        "windows_per_file",
        "fps",
        "frame_width",
        "daily_call_cap",
        "poll_s",
        "quiet_polls",
        "min_age_s",
        "request_timeout_s",
        "api_port",
    }
)
PATH_KEYS: frozenset[str] = frozenset({"recordings_dir", "reports_dir", "ledger_path"})


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

    return Config(**values)  # type: ignore[arg-type]
