import threading
from datetime import UTC, datetime
from pathlib import Path

from round_review.errors import OllamaError
from round_review.server.jobs import Job, JobQueue, ProgressFn

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class FakeRun:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[Path] = []
        self.fail = fail
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, path: Path, on_progress: ProgressFn) -> None:
        self.calls.append(path)
        self.started.set()
        on_progress(0, 2)
        self.release.wait(timeout=5)
        on_progress(2, 2)
        if self.fail:
            raise self.fail


def test_submit_returns_queued_job() -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    job = q.submit(Path("/v/a.mp4"), key="k1")
    assert isinstance(job, Job)
    assert job.status == "queued"
    assert job.key == "k1"
    assert job.created_at == NOW
    assert q.get(job.id) == job
    assert q.list() == [job]


def test_duplicate_submit_returns_existing_job() -> None:
    q = JobQueue(FakeRun(), clock=lambda: NOW)
    a = q.submit(Path("/v/a.mp4"), key="k1")
    b = q.submit(Path("/v/a.mp4"), key="k1")
    assert a.id == b.id
    assert len(q.list()) == 1


def test_worker_runs_jobs_in_order_with_progress(tmp_path: Path) -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    q.start()
    try:
        first = q.submit(tmp_path / "a.mp4", key="ka")
        second = q.submit(tmp_path / "b.mp4", key="kb")
        assert run.started.wait(timeout=2)
        running = q.get(first.id)
        assert running is not None and running.status == "running"
        assert (running.windows_done, running.windows_total) == (0, 2)
        assert q.get(second.id).status == "queued"  # type: ignore[union-attr]
        run.release.set()
        assert q.wait_idle(timeout=5)
        done_first = q.get(first.id)
        assert done_first is not None and done_first.status == "done"
        assert (done_first.windows_done, done_first.windows_total) == (2, 2)
        assert done_first.finished_at == NOW
        assert q.get(second.id).status == "done"  # type: ignore[union-attr]
        assert run.calls == [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    finally:
        q.stop()


def test_failed_job_records_error_class_and_keeps_worker_alive(tmp_path: Path) -> None:
    run = FakeRun(fail=OllamaError("connection refused"))
    q = JobQueue(run, clock=lambda: NOW)
    q.start()
    try:
        job = q.submit(tmp_path / "a.mp4", key="ka")
        run.release.set()
        assert q.wait_idle(timeout=5)
        failed = q.get(job.id)
        assert failed is not None and failed.status == "failed"
        assert failed.error == "OllamaError: connection refused"
        # a finished job can be resubmitted
        again = q.submit(tmp_path / "a.mp4", key="ka")
        assert again.id != job.id
        assert q.wait_idle(timeout=5)
    finally:
        q.stop()


def test_get_unknown_is_none() -> None:
    assert JobQueue(FakeRun(), clock=lambda: NOW).get("nope") is None


def test_active_job_for_key() -> None:
    q = JobQueue(FakeRun(), clock=lambda: NOW)
    job = q.submit(Path("/v/a.mp4"), key="k1")
    assert q.active_for_key("k1") == job
    assert q.active_for_key("zz") is None
