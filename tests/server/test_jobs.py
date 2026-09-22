import threading
from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.context import PlayerContext
from round_review.coaching.question import QuestionSpec
from round_review.errors import OllamaError
from round_review.server.jobs import Job, JobOptions, JobQueue, ProgressFn

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class FakeRun:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[Path] = []
        self.contexts: list[PlayerContext] = []
        self.options: list[JobOptions] = []
        self.fail = fail
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(
        self, path: Path, context: PlayerContext, options: JobOptions, on_progress: ProgressFn
    ) -> None:
        self.calls.append(path)
        self.contexts.append(context)
        self.options.append(options)
        self.started.set()
        on_progress(0, 2)
        self.release.wait(timeout=5)
        on_progress(2, 2)
        if self.fail:
            raise self.fail


def test_submit_returns_queued_job() -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    job = q.submit(Path("/v/a.mp4"), key="k1", context=PlayerContext(rank="Gold 2"))
    assert isinstance(job, Job)
    assert job.status == "queued"
    assert job.context == PlayerContext(rank="Gold 2")
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
        assert run.contexts == [PlayerContext(), PlayerContext()]
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


def test_submit_carries_force_and_coverage_overrides() -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    job = q.submit(
        Path("/v/a.mp4"),
        key="k1",
        options=JobOptions(force=True, coverage="sampled", max_span_s=60.0, max_windows=8),
    )
    assert job.options.force is True
    assert job.options.coverage == "sampled"
    assert job.options.max_span_s == 60.0
    assert job.options.max_windows == 8


def test_worker_passes_options_to_the_runner(tmp_path: Path) -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    q.start()
    try:
        q.submit(tmp_path / "a.mp4", key="ka", options=JobOptions(force=True))
        run.release.set()
        assert q.wait_idle(timeout=5)
        assert run.options[0].force is True
    finally:
        q.stop()


def test_force_resubmit_of_a_finished_clip_is_a_new_job(tmp_path: Path) -> None:
    run = FakeRun()
    q = JobQueue(run, clock=lambda: NOW)
    q.start()
    try:
        first = q.submit(tmp_path / "a.mp4", key="ka")
        run.release.set()
        assert q.wait_idle(timeout=5)
        again = q.submit(tmp_path / "a.mp4", key="ka", options=JobOptions(force=True))
        assert again.id != first.id
        assert q.wait_idle(timeout=5)
        assert len(run.calls) == 2
    finally:
        q.stop()


def test_a_question_job_runs_on_the_same_worker(tmp_path: Path) -> None:
    """One worker, so a question never competes with a review for the GPU."""
    run = FakeRun()
    asked: list[QuestionSpec] = []

    def run_question(path: Path, context: PlayerContext, spec: QuestionSpec) -> str:
        asked.append(spec)
        return "an answer"

    q = JobQueue(run, clock=lambda: NOW, run_question=run_question)
    q.start()
    try:
        job = q.submit_question(tmp_path / "a.mp4", key="ka", spec=QuestionSpec(40.0, 52.0, "why?"))
        assert job.kind == "question"
        assert job.question is not None and job.question.question == "why?"
        run.release.set()
        assert q.wait_idle(timeout=5)
        done = q.get(job.id)
        assert done is not None and done.status == "done"
        assert done.answer == "an answer"
        assert asked[0].question == "why?"
        assert run.calls == []  # no review was started
    finally:
        q.stop()


def test_two_questions_about_the_same_clip_are_both_answered(tmp_path: Path) -> None:
    def run_question(path: Path, context: PlayerContext, spec: QuestionSpec) -> str:
        return f"answer to {spec.question}"

    q = JobQueue(FakeRun(), clock=lambda: NOW, run_question=run_question)
    q.start()
    try:
        first = q.submit_question(tmp_path / "a.mp4", "ka", QuestionSpec(1.0, 2.0, "one"))
        second = q.submit_question(tmp_path / "a.mp4", "ka", QuestionSpec(3.0, 4.0, "two"))
        assert first.id != second.id  # never deduplicated: they are different questions
        assert q.wait_idle(timeout=5)
        assert q.get(second.id).answer == "answer to two"  # type: ignore[union-attr]
    finally:
        q.stop()


def test_a_question_that_fails_records_the_error(tmp_path: Path) -> None:
    def boom(path: Path, context: PlayerContext, spec: QuestionSpec) -> str:
        raise OllamaError("refused")

    q = JobQueue(FakeRun(), clock=lambda: NOW, run_question=boom)
    q.start()
    try:
        job = q.submit_question(tmp_path / "a.mp4", "ka", QuestionSpec(1.0, 2.0, "why?"))
        assert q.wait_idle(timeout=5)
        failed = q.get(job.id)
        assert failed is not None and failed.status == "failed"
        assert failed.error == "OllamaError: refused"
    finally:
        q.stop()


def test_a_review_is_still_deduplicated_while_questions_are_not(tmp_path: Path) -> None:
    q = JobQueue(FakeRun(), clock=lambda: NOW)
    a = q.submit(tmp_path / "a.mp4", key="ka")
    b = q.submit(tmp_path / "a.mp4", key="ka")
    assert a.id == b.id
