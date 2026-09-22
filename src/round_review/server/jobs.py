"""Single-worker job queue. One review at a time so the GPU is never shared."""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from round_review.coaching.context import PlayerContext
from round_review.coaching.question import QuestionSpec

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]
JobKind = Literal["review", "question"]
ACTIVE: frozenset[str] = frozenset({"queued", "running"})
ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class JobOptions:
    """Per-job overrides. `force` re-reviews a clip already in the ledger; the coverage
    fields override the configured review span for this job only."""

    force: bool = False
    coverage: str | None = None
    max_span_s: float | None = None
    max_windows: int | None = None


RunFn = Callable[[Path, PlayerContext, JobOptions, ProgressFn], object]
# A question returns its answer, which is stored on the job for the app to collect.
QuestionFn = Callable[[Path, PlayerContext, QuestionSpec], object]


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
    context: PlayerContext = field(default_factory=PlayerContext)
    options: JobOptions = field(default_factory=JobOptions)
    kind: JobKind = "review"
    # Set on question jobs only: what was asked, and the answer once it is known.
    question: QuestionSpec | None = None
    answer: object | None = None


class JobQueue:
    """Owns the worker thread and the job table. All public methods are thread-safe and
    return snapshots, never live Job objects."""

    def __init__(
        self,
        run: RunFn,
        clock: Callable[[], datetime],
        run_question: QuestionFn | None = None,
    ) -> None:
        self._run = run
        self._run_question = run_question
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

    def submit(
        self,
        path: Path,
        key: str,
        context: PlayerContext | None = None,
        options: JobOptions | None = None,
    ) -> Job:
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
                context=context or PlayerContext(),
                options=options or JobOptions(),
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._pending += 1
        self._queue.put(job.id)
        return dataclasses.replace(job)

    def submit_question(
        self,
        path: Path,
        key: str,
        spec: QuestionSpec,
        context: PlayerContext | None = None,
    ) -> Job:
        """Queue a question. Never deduplicated: two questions about the same clip are two
        different questions, and the player is waiting for both answers."""
        with self._lock:
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
                context=context or PlayerContext(),
                kind="question",
                question=spec,
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
                if job.kind == "question":
                    if self._run_question is None or job.question is None:
                        raise RuntimeError("this queue cannot answer questions")
                    answer = self._run_question(job.path, job.context, job.question)
                    self._set(job_id, answer=answer)
                else:
                    self._run(job.path, job.context, job.options, on_progress)
            except Exception as exc:  # the worker must survive any job failure
                log.error(
                    "job %s (%s) failed: %s: %s", job_id, job.path.name, type(exc).__name__, exc
                )
                self._finish(job_id, "failed", f"{type(exc).__name__}: {exc}")
            else:
                self._finish(job_id, "done", None)
