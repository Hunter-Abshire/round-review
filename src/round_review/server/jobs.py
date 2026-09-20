"""Single-worker job queue. One review at a time so the GPU is never shared."""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]
ACTIVE: frozenset[str] = frozenset({"queued", "running"})
ProgressFn = Callable[[int, int], None]
RunFn = Callable[[Path, ProgressFn], object]


@dataclass(slots=True)
class Job:
    id: str
    path: Path
    key: str
    status: JobStatus
    windows_done: int
    windows_total: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class JobQueue:
    """Owns the worker thread and the job table. All public methods are thread-safe and
    return snapshots, never live Job objects."""

    def __init__(self, run: RunFn, clock: Callable[[], datetime]) -> None:
        self._run = run
        self._clock = clock
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending = 0
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, name="round-review-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=5)
        self._thread = None

    def wait_idle(self, timeout: float) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout=timeout)

    # -- queries ---------------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dataclasses.replace(job) if job else None

    def list(self) -> list[Job]:
        with self._lock:
            return [dataclasses.replace(self._jobs[i]) for i in self._order]

    def active_for_key(self, key: str) -> Job | None:
        with self._lock:
            return next(
                (
                    dataclasses.replace(j)
                    for j in self._jobs.values()
                    if j.key == key and j.status in ACTIVE
                ),
                None,
            )

    def latest_for_key(self, key: str) -> Job | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.key == key:
                    return dataclasses.replace(job)
            return None

    # -- commands --------------------------------------------------------------------------

    def submit(self, path: Path, key: str) -> Job:
        with self._lock:
            existing = next(
                (j for j in self._jobs.values() if j.key == key and j.status in ACTIVE), None
            )
            if existing:
                return dataclasses.replace(existing)
            job = Job(
                id=uuid.uuid4().hex[:12],
                path=path,
                key=key,
                status="queued",
                windows_done=0,
                windows_total=0,
                error=None,
                created_at=self._clock(),
                finished_at=None,
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._pending += 1
        self._queue.put(job.id)
        return dataclasses.replace(job)

    # -- worker ----------------------------------------------------------------------------

    def _set(self, job_id: str, **changes: object) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for name, value in changes.items():
                setattr(job, name, value)

    def _finish(self, job_id: str, status: JobStatus, error: str | None) -> None:
        with self._idle:
            job = self._jobs[job_id]
            job.status = status
            job.error = error
            job.finished_at = self._clock()
            self._pending -= 1
            self._idle.notify_all()

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            job = self.get(job_id)
            assert job is not None
            self._set(job_id, status="running")

            def on_progress(done: int, total: int, job_id: str = job_id) -> None:
                self._set(job_id, windows_done=done, windows_total=total)

            try:
                self._run(job.path, on_progress)
            except Exception as exc:  # the worker must survive any job failure
                log.error(
                    "job %s (%s) failed: %s: %s", job_id, job.path.name, type(exc).__name__, exc
                )
                self._finish(job_id, "failed", f"{type(exc).__name__}: {exc}")
            else:
                self._finish(job_id, "done", None)
