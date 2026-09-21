"""Loopback HTTP API for the desktop app. Never bind this to anything but 127.0.0.1."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from round_review.coaching.context import context_from_mapping
from round_review.coaching.knowledge import load_knowledge
from round_review.config import Config
from round_review.errors import LedgerError, VideoError
from round_review.ledger import LedgerEntry, read_ledger
from round_review.pipeline import key_for, report_dir_for
from round_review.report.json_report import JSON_REPORT_FILENAME, load_report_json
from round_review.server.jobs import Job, JobQueue
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

LEDGER_STATUS_TO_CLIP: dict[str, str] = {"ok": "done", "failed": "failed", "skipped": "skipped"}


class SubmitJob(BaseModel):
    path: str
    context: dict[str, Any] | None = None


def _job_json(job: Job) -> dict[str, Any]:
    data = dataclasses.asdict(job)
    data["path"] = str(job.path)
    data["context"] = dataclasses.asdict(job.context)
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


def create_app(config: Config, jobs: JobQueue) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="round-review", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.config = config

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": config.model}

    @app.get("/api/clips")
    def clips() -> dict[str, Any]:
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
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "key": key,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                    "status": status,
                    "job_id": job_id,
                    "error": error,
                }
            )
        return {"clips": items, "warning": None}

    @app.post("/api/jobs", status_code=202)
    def submit(body: SubmitJob) -> dict[str, Any]:
        if config.recordings_dir is None:
            raise HTTPException(status_code=400, detail="recordings_dir is not configured")
        path = Path(body.path).resolve()
        if not path.is_relative_to(config.recordings_dir.resolve()):
            raise HTTPException(status_code=400, detail="path is outside recordings_dir")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="clip not found")
        return _job_json(jobs.submit(path, key_for(path), context_from_mapping(body.context)))

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
