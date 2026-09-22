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
    # Where what-each-clip-is is remembered. Defaults beside the ledger.
    identities_path: Path | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3-vl:8b"
    num_ctx: int = DEFAULT_NUM_CTX
    window_s: float = 12.0
    # "full" tiles the whole recording; "sampled" takes `windows_per_file` spread evenly.
    coverage: str = "full"
    # Seconds between clock samples when scanning a recording for round boundaries.
    scan_interval_s: float = 2.0
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
    # A folder of your own markdown or text notes (lineups, team calls, a coach's advice).
    # Their sections are searched alongside the bundled briefs when you ask a question.
    notes_dir: Path | None = None
    # How many reference passages an asked question may carry, and their size budget.
    # 0 switches retrieval off entirely.
    reference_passages: int = 4
    max_reference_chars: int = 4000
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


def identities_file(config: Config) -> Path:
    """Where clip identities live: the configured path, else beside the ledger."""
    return config.identities_path or config.ledger_path.parent / "identities.json"


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
    {
        "recordings_dir",
        "reports_dir",
        "ledger_path",
        "identities_path",
        "hud_templates_path",
        "notes_dir",
    }
)
COVERAGE_MODES: frozenset[str] = frozenset({"full", "sampled", "rounds"})


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Everything the app needs to render one setting without knowing what it means."""

    name: str
    group: str
    label: str
    help: str
    kind: str  # bool | int | float | text | path | choice
    choices: tuple[str, ...] = ()
    # Set when the choices are only known at runtime, e.g. the models Ollama has pulled.
    choices_from: str = ""
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    advanced: bool = False


# Derived, internal or write-once locations: changing these from a settings form would move
# where past reviews live, so they stay in the file for anyone who really wants them.
EXCLUDED_FROM_UI: frozenset[str] = frozenset(
    {
        "reports_dir",
        "ledger_path",
        "identities_path",
        "hud_templates_path",
        "api_port",
        "player_notes",
    }
)

GROUPS: tuple[str, ...] = (
    "Recordings",
    "Model",
    "How much to review",
    "Reading the scene",
    "Round clock",
    "Questions",
    "Watching",
    "Tools",
)

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "recordings_dir",
        "Recordings",
        "Recordings folder",
        "Where Outplayed writes your clips.",
        "path",
    ),
    FieldSpec(
        "notes_dir",
        "Recordings",
        "Notes folder",
        "Your own markdown or text notes, searched when you ask a question.",
        "path",
    ),
    FieldSpec(
        "model",
        "Model",
        "Model",
        "The Ollama vision model that reviews your clips.",
        "choice",
        choices_from="ollama_models",
    ),
    FieldSpec(
        "ollama_url",
        "Model",
        "Ollama address",
        "Where Ollama is listening. Local unless you run it elsewhere.",
        "text",
        advanced=True,
    ),
    FieldSpec(
        "num_ctx",
        "Model",
        "Context window",
        "Tokens the model may hold. Raise it if findings come back empty or cut off.",
        "int",
        minimum=2048,
        maximum=131072,
        unit="tokens",
    ),
    FieldSpec(
        "request_timeout_s",
        "Model",
        "Call timeout",
        "How long one model call may take before it is abandoned.",
        "float",
        minimum=30,
        maximum=3600,
        unit="seconds",
    ),
    FieldSpec(
        "ollama_keep_alive",
        "Model",
        "Keep model loaded",
        "How long Ollama holds the model in memory between calls.",
        "text",
        advanced=True,
    ),
    FieldSpec(
        "daily_call_cap",
        "Model",
        "Daily call cap",
        "Model calls allowed per day. 0 means unlimited, which is usual for local inference.",
        "int",
        minimum=0,
    ),
    FieldSpec(
        "coverage",
        "How much to review",
        "Coverage",
        "Review the whole recording, the moments that decide rounds, or a few windows "
        "spread across it. Rounds needs a trained HUD clock and falls back to the whole "
        "recording without one.",
        "choice",
        choices=("rounds", "full", "sampled"),
    ),
    FieldSpec(
        "max_span_s",
        "How much to review",
        "Review only the first",
        "Seconds of gameplay to review. 0 reviews the whole recording.",
        "float",
        minimum=0,
        unit="seconds",
    ),
    FieldSpec(
        "max_windows",
        "How much to review",
        "Window budget",
        "Most windows to review. 0 is unlimited; a budget stays spread across the clip.",
        "int",
        minimum=0,
    ),
    FieldSpec(
        "windows_per_file",
        "How much to review",
        "Sampled windows",
        "How many windows sampled coverage takes.",
        "int",
        minimum=1,
        maximum=50,
    ),
    FieldSpec(
        "window_s",
        "How much to review",
        "Window length",
        "Seconds of gameplay judged together.",
        "float",
        minimum=0.1,
        maximum=120,
        unit="seconds",
    ),
    FieldSpec(
        "edge_skip_s",
        "How much to review",
        "Skip start and end",
        "Seconds ignored at each end, for loading and end screens.",
        "float",
        minimum=0,
        unit="seconds",
    ),
    FieldSpec(
        "fps",
        "How much to review",
        "Frames per second",
        "Frames sampled inside each window. Lower is faster and blinder.",
        "float",
        minimum=0.1,
        maximum=10,
    ),
    FieldSpec(
        "frame_width",
        "How much to review",
        "Frame width",
        "Pixels wide each frame is sent at. Lower is faster and harder to read.",
        "int",
        minimum=320,
        maximum=2560,
        unit="px",
    ),
    FieldSpec(
        "situation_pass",
        "Reading the scene",
        "Read the scene first",
        "Ask what is on screen before coaching. Off is faster but loses agent and map detection.",
        "bool",
    ),
    FieldSpec(
        "situation_frames",
        "Reading the scene",
        "Frames for the scene read",
        (
            "Frames the scene pass carries. It runs for every window, so this drives how "
            "long a review takes. 0 sends all of them."
        ),
        "int",
        minimum=0,
        maximum=60,
    ),
    FieldSpec(
        "coach_frames",
        "Reading the scene",
        "Frames for coaching",
        "Frames the coaching pass carries. 0 sends every frame of the window.",
        "int",
        minimum=0,
        maximum=60,
    ),
    FieldSpec(
        "abstain_streak_limit",
        "Reading the scene",
        "Give up after",
        "Windows skipped in a row before a review stops. 0 never gives up.",
        "int",
        minimum=0,
    ),
    FieldSpec(
        "hud_check",
        "Round clock",
        "Read the round clock",
        "Read the timer without the model and overrule it when it misreads the phase.",
        "bool",
    ),
    FieldSpec(
        "hud_timer_region",
        "Round clock",
        "Timer region",
        "Where the timer sits, as x,y,w,h fractions of the frame. Check it with hud crop.",
        "text",
        advanced=True,
    ),
    FieldSpec(
        "hud_threshold",
        "Round clock",
        "Brightness cutoff",
        "Separates the white timer from the scene. -1 picks one automatically.",
        "int",
        minimum=-1,
        maximum=255,
        advanced=True,
    ),
    FieldSpec(
        "scan_interval_s",
        "Round clock",
        "Clock scan interval",
        "Seconds between clock readings when finding round boundaries. Smaller is more "
        "precise and slower.",
        "float",
        minimum=0.5,
        maximum=30,
        unit="s",
        advanced=True,
    ),
    FieldSpec(
        "hud_min_confidence",
        "Round clock",
        "Minimum confidence",
        "How sure a digit match must be before the clock is trusted.",
        "float",
        minimum=0,
        maximum=1,
        advanced=True,
    ),
    FieldSpec(
        "buy_phase_max_s",
        "Round clock",
        "Buy phase maximum",
        "A clock above this proves the round is live. Rarely needs changing.",
        "float",
        minimum=1,
        maximum=180,
        unit="seconds",
        advanced=True,
    ),
    FieldSpec(
        "question_frames",
        "Questions",
        "Frames per question",
        "Frames sent when you ask about a moment.",
        "int",
        minimum=1,
        maximum=30,
    ),
    FieldSpec(
        "max_question_span_s",
        "Questions",
        "Longest question range",
        "The most footage one question may cover.",
        "float",
        minimum=1,
        maximum=600,
        unit="seconds",
    ),
    FieldSpec(
        "reference_passages",
        "Questions",
        "Reference passages",
        "How much of the briefs and your notes a question may carry. 0 turns lookup off.",
        "int",
        minimum=0,
        maximum=20,
    ),
    FieldSpec(
        "max_reference_chars",
        "Questions",
        "Reference size",
        "Character budget for retrieved passages.",
        "int",
        minimum=0,
        maximum=40000,
        advanced=True,
    ),
    FieldSpec(
        "poll_s",
        "Watching",
        "Check every",
        "How often the watcher looks for new recordings.",
        "float",
        minimum=1,
        unit="seconds",
    ),
    FieldSpec(
        "quiet_polls",
        "Watching",
        "Unchanged checks",
        "Checks a file must be unchanged for before it counts as finished.",
        "int",
        minimum=1,
    ),
    FieldSpec(
        "min_age_s",
        "Watching",
        "Minimum age",
        "How old a recording must be before it is reviewed.",
        "float",
        minimum=0,
        unit="seconds",
    ),
    FieldSpec(
        "ffmpeg_path",
        "Tools",
        "ffmpeg",
        "Path to ffmpeg, if it is not on PATH.",
        "text",
        advanced=True,
    ),
    FieldSpec(
        "ffprobe_path",
        "Tools",
        "ffprobe",
        "Path to ffprobe, if it is not on PATH.",
        "text",
        advanced=True,
    ),
)

_FIELDS_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FIELDS}


def field_spec(name: str) -> FieldSpec:
    return _FIELDS_BY_NAME[name]


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


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "/").replace('"', '\\"')
    return f'"{text}"'


def _read_raw(path: Path) -> dict[str, object]:
    """The settings already in the file, so an edit changes one line and keeps the rest."""
    try:
        return dict(_read_toml(path))
    except ConfigError:
        raise
    except OSError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{path}: cannot read config: {exc}") from exc


def write_config(path: Path, updates: Mapping[str, object]) -> Path:
    """Apply `updates` to the configuration file, validating before anything is written.

    A value of None removes the setting, so it falls back to its default. The file is
    rewritten grouped and commented from the field metadata, so it stays readable by hand;
    any comments you added yourself are not preserved.
    """
    for key in updates:
        if key in EXCLUDED_FROM_UI:
            raise ConfigError(f"{key} is not editable from the app; set it in the file directly")
        if key not in _FIELDS_BY_NAME:
            raise ConfigError(f"unknown setting {key}")

    merged: dict[str, object] = _read_raw(path)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value

    # Validate by loading what we are about to write, so a bad value never reaches the file.
    scratch = path.parent / f".{path.name}.check"
    lines = _render_config(merged)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(lines, encoding="utf-8")
        load_config(scratch, env={}, data_dir=path.parent)
    finally:
        scratch.unlink(missing_ok=True)

    path.write_text(lines, encoding="utf-8")
    return path


def _render_config(values: Mapping[str, object]) -> str:
    lines = [
        "# round-review configuration.",
        "# Written by the app; edit by hand if you prefer, the app will keep your values.",
        "",
    ]
    written: set[str] = set()
    for group in GROUPS:
        in_group = [f for f in FIELDS if f.group == group and f.name in values]
        if not in_group:
            continue
        lines.append(f"# {group}")
        for spec in in_group:
            lines.append(f"# {spec.help}")
            lines.append(f"{spec.name} = {_toml_value(values[spec.name])}")
            written.add(spec.name)
        lines.append("")
    leftovers = {k: v for k, v in values.items() if k not in written}
    if leftovers:
        lines.append("# Set by hand")
        lines += [f"{key} = {_toml_value(value)}" for key, value in sorted(leftovers.items())]
        lines.append("")
    return "\n".join(lines)
