"""Loopback HTTP API for the desktop app. Never bind this to anything but 127.0.0.1."""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from round_review.coaching.context import context_from_mapping
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.question import QuestionSpec
from round_review.config import (
    COVERAGE_MODES,
    ENV_PREFIX,
    FIELDS,
    GROUPS,
    Config,
    load_config,
    write_config,
)
from round_review.errors import LedgerError, RoundReviewError, VideoError
from round_review.ledger import LedgerEntry, read_ledger
from round_review.llm.models import list_models
from round_review.pacing import estimate_seconds, format_duration
from round_review.pipeline import key_for, report_dir_for
from round_review.report.json_report import JSON_REPORT_FILENAME, load_report_json
from round_review.server.jobs import Job, JobOptions, JobQueue
from round_review.video.probe import CommandRunner, SubprocessRunner, probe
from round_review.video.windows import estimate_window_count
from round_review.vision.digits import CLOCK_CHARACTERS, DigitTemplates
from round_review.watcher import list_candidates

KEY_RE = re.compile(r"^[0-9a-f]{16}$")
FRAME_RE = re.compile(r"^(w\d{2}_\d{3}|e\d{2}_\d{2})\.jpg$")

RANKS: tuple[str, ...] = (
    "Iron",
    "Bronze",
    "Silver",
    "Gold",
    "Platinum",
    "Diamond",
    "Ascendant",
    "Immortal",
    "Radiant",
)

LEDGER_STATUS_TO_CLIP: dict[str, str] = {
    "ok": "done",
    "partial": "partial",
    "failed": "failed",
    "skipped": "skipped",
}


@dataclasses.dataclass(slots=True)
class ConfigHolder:
    """The live configuration, so a change from the settings screen applies to the next job
    without restarting the app. Read it per request; never capture `current` in a closure."""

    current: Config
    path: Path


class SaveConfig(BaseModel):
    values: dict[str, Any]


class AskQuestion(BaseModel):
    path: str
    question: str = Field(min_length=1)
    start_s: float = Field(ge=0)
    # Omitted means "about this moment": the pipeline widens a zero-length range.
    end_s: float | None = None

    context: dict[str, Any] | None = None


class SubmitJob(BaseModel):
    path: str
    context: dict[str, Any] | None = None
    force: bool = False
    coverage: Literal["full", "sampled"] | None = None
    max_span_s: float | None = Field(default=None, ge=0)
    max_windows: int | None = Field(default=None, ge=0)


def _job_json(job: Job) -> dict[str, Any]:
    data = dataclasses.asdict(job)
    data["path"] = str(job.path)
    data["context"] = dataclasses.asdict(job.context)
    data["options"] = dataclasses.asdict(job.options)
    data["question"] = dataclasses.asdict(job.question) if job.question else None
    data["answer"] = job.answer
    data["created_at"] = job.created_at.isoformat()
    data["finished_at"] = job.finished_at.isoformat() if job.finished_at else None
    return data


def _latest_ledger(entries: list[LedgerEntry], key: str) -> LedgerEntry | None:
    return next((e for e in reversed(entries) if e.key == key), None)


def _clip_status(
    jobs: JobQueue, entries: list[LedgerEntry], key: str
) -> tuple[str, str | None, str | None]:
    """(status, job_id, error). Precedence: active job > latest job > ledger > new."""
    active = jobs.active_for_key(key)
    if active:
        return active.status, active.id, None
    latest = jobs.latest_for_key(key)
    if latest:
        return latest.status, latest.id, latest.error
    entry = _latest_ledger(entries, key)
    if entry:
        return LEDGER_STATUS_TO_CLIP[entry.status], None, entry.error
    return "new", None, None


def _find_clip(config: Config, key: str) -> Path | None:
    if config.recordings_dir is None or not config.recordings_dir.is_dir():
        return None
    for path in list_candidates(config.recordings_dir):
        try:
            if key_for(path) == key:
                return path
        except VideoError:
            continue
    return None


def _valid_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise HTTPException(status_code=404, detail="unknown key")
    return key


def _duration_reader(runner: CommandRunner) -> Callable[[Path, str], float | None]:
    """Probe durations for the clip list, cached by recording key so repeated listings do
    not re-run ffprobe. An unreadable file reports no duration rather than failing the list."""
    cache: dict[str, float | None] = {}

    def duration_of(path: Path, key: str) -> float | None:
        if key not in cache:
            try:
                cache[key] = probe(path, runner).duration_s
            except RoundReviewError:
                cache[key] = None
        return cache[key]

    return duration_of


def create_app(
    holder: ConfigHolder, jobs: JobQueue, probe_runner: CommandRunner | None = None
) -> FastAPI:
    config = holder.current
    duration_of = _duration_reader(probe_runner or SubprocessRunner(config.ffprobe_path))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="round-review", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.config = holder
    app.state.config_path = holder.path

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": holder.current.model}

    @app.get("/api/clips")
    def clips() -> dict[str, Any]:
        config = holder.current
        if config.recordings_dir is None or not config.recordings_dir.is_dir():
            return {"clips": [], "warning": "recordings_dir is not configured or does not exist"}
        try:
            entries = read_ledger(config.ledger_path)
        except LedgerError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        items: list[dict[str, Any]] = []
        for path in list_candidates(config.recordings_dir):
            try:
                st = path.stat()
                key = key_for(path)
            except (OSError, VideoError):
                continue
            status, job_id, error = _clip_status(jobs, entries, key)
            duration_s = duration_of(path, key)
            windows = (
                estimate_window_count(
                    duration_s,
                    window_s=config.window_s,
                    coverage=config.coverage,  # type: ignore[arg-type]
                    windows_per_file=config.windows_per_file,
                    edge_skip_s=config.edge_skip_s,
                    max_windows=config.max_windows,
                    max_span_s=config.max_span_s,
                )
                if duration_s
                else None
            )
            # Estimated from what past reviews on this machine actually took, not from
            # hardware: a full review can be hours, and that should not be a surprise.
            estimated = estimate_seconds(windows or 0, entries)
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "key": key,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                    "duration_s": duration_s,
                    "estimated_windows": windows,
                    "estimated_seconds": estimated,
                    "estimated_time": format_duration(estimated) if estimated else None,
                    "status": status,
                    "job_id": job_id,
                    "error": error,
                }
            )
        return {"clips": items, "warning": None}

    def _clip_path(raw: str) -> Path:
        """Resolve a requested clip, refusing anything outside the recordings folder."""
        config = holder.current
        if config.recordings_dir is None:
            raise HTTPException(status_code=400, detail="recordings_dir is not configured")
        path = Path(raw).resolve()
        if not path.is_relative_to(config.recordings_dir.resolve()):
            raise HTTPException(status_code=400, detail="path is outside recordings_dir")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="clip not found")
        return path

    @app.post("/api/jobs", status_code=202)
    def submit(body: SubmitJob) -> dict[str, Any]:
        path = _clip_path(body.path)
        options = JobOptions(
            force=body.force,
            coverage=body.coverage,
            max_span_s=body.max_span_s,
            max_windows=body.max_windows,
        )
        return _job_json(
            jobs.submit(path, key_for(path), context_from_mapping(body.context), options)
        )

    def hud_missing() -> list[str]:
        config = holder.current
        if not config.hud_templates_path:
            return list(CLOCK_CHARACTERS)
        try:
            return DigitTemplates.load(config.hud_templates_path).missing()
        except RoundReviewError:
            return list(CLOCK_CHARACTERS)

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        """Review defaults, so the app can show what a review will do before starting one."""
        config = holder.current
        return {
            "model": config.model,
            "coverage": config.coverage,
            "coverage_modes": sorted(COVERAGE_MODES),
            "window_s": config.window_s,
            "windows_per_file": config.windows_per_file,
            "max_span_s": config.max_span_s,
            "max_windows": config.max_windows,
            "fps": config.fps,
            "situation_pass": config.situation_pass,
            "question_frames": config.question_frames,
            "max_question_span_s": config.max_question_span_s,
            "daily_call_cap": config.daily_call_cap,
            "hud_check": config.hud_check,
            # A HUD check with no learned digits does nothing, so say so rather than
            # leaving the player to wonder why nothing changed.
            "hud_ready": not hud_missing() if config.hud_check else False,
            "hud_missing_characters": hud_missing() if config.hud_check else [],
        }

    @app.get("/api/config")
    def read_config() -> dict[str, Any]:
        """Every editable setting with its value and what it means, so the app can render a
        settings screen without knowing anything about the settings."""
        config = holder.current
        models = [m.name for m in list_models(config.ollama_url)]
        fields: list[dict[str, Any]] = []
        for spec in FIELDS:
            value = getattr(config, spec.name)
            env_name = f"{ENV_PREFIX}{spec.name.upper()}"
            fields.append(
                {
                    "name": spec.name,
                    "group": spec.group,
                    "label": spec.label,
                    "help": spec.help,
                    "kind": spec.kind,
                    "choices": models
                    if spec.choices_from == "ollama_models"
                    else list(spec.choices),
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "unit": spec.unit,
                    "advanced": spec.advanced,
                    "value": str(value) if isinstance(value, Path) else value,
                    "default": _default_for(spec.name),
                    # A variable in the environment wins over the file, so say so rather
                    # than letting a saved value appear to do nothing.
                    "overridden_by_env": env_name if env_name in os.environ else None,
                }
            )
        return {"path": str(holder.path), "groups": list(GROUPS), "fields": fields}

    @app.put("/api/config")
    def save_config(body: SaveConfig) -> dict[str, Any]:
        try:
            write_config(holder.path, body.values)
            holder.current = load_config(holder.path, os.environ)
        except RoundReviewError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"saved": True, "path": str(holder.path)}

    @app.get("/api/knowledge")
    def knowledge() -> dict[str, Any]:
        k = load_knowledge()
        return {
            "agents": [
                {"id": a.id, "name": a.name, "role": a.role}
                for a in sorted(k.agents.values(), key=lambda a: a.name)
            ],
            "maps": [
                {"id": m.id, "name": m.name} for m in sorted(k.maps.values(), key=lambda m: m.name)
            ],
            "ranks": list(RANKS),
            "checklist": [
                {
                    "id": cat.id,
                    "name": cat.name,
                    "checks": [{"id": c.id, "check": c.check, "fix": c.fix} for c in cat.checks],
                }
                for cat in k.checklist.categories
            ],
        }

    @app.post("/api/ask", status_code=202)
    def ask(body: AskQuestion) -> dict[str, Any]:
        """Ask about one stretch of a clip. Queued on the same worker as reviews, so a
        question never competes with a running review for the GPU."""
        if not body.question.strip():
            raise HTTPException(status_code=422, detail="question must not be empty")
        path = _clip_path(body.path)
        spec = QuestionSpec(
            start_s=body.start_s,
            end_s=body.end_s if body.end_s is not None else body.start_s,
            question=body.question,
        )
        return _job_json(
            jobs.submit_question(path, key_for(path), spec, context_from_mapping(body.context))
        )

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": [_job_json(j) for j in jobs.list()]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return _job_json(job)

    @app.get("/api/reports/{key}")
    def get_report(key: str) -> dict[str, Any]:
        _valid_key(key)
        clip = _find_clip(config, key)
        path = report_dir_for(config, clip) / JSON_REPORT_FILENAME if clip else None
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="no report for key")
        return load_report_json(path)

    @app.get("/api/media/{key}/video")
    def video(key: str) -> FileResponse:
        clip = _find_clip(config, _valid_key(key))
        if clip is None:
            raise HTTPException(status_code=404, detail="clip not found")
        return FileResponse(clip, media_type="video/mp4")

    @app.get("/api/media/{key}/frames/{name}")
    def frame(key: str, name: str) -> FileResponse:
        clip = _find_clip(config, _valid_key(key))
        if clip is None or not FRAME_RE.match(name):
            raise HTTPException(status_code=404, detail="frame not found")
        path = report_dir_for(config, clip) / "frames" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(path, media_type="image/jpeg")

    return app


def _default_for(name: str) -> Any:
    """The value a setting falls back to when it is not in the file."""
    field = next(f for f in dataclasses.fields(Config) if f.name == name)
    default = field.default
    if default is dataclasses.MISSING:
        return None
    return str(default) if isinstance(default, Path) else default
