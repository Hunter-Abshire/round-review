from datetime import UTC, datetime
from pathlib import Path

import pytest

from round_review.errors import CapExceeded, OllamaError, VideoError
from round_review.watcher import FileSnapshot, WatchState, is_stable, poll_once, watch_loop


class StopWatching(Exception):
    pass


def snap(size: int, mtime: float) -> FileSnapshot:
    return FileSnapshot(size_bytes=size, mtime=mtime)


def test_is_stable_requires_quiet_polls_and_age() -> None:
    assert not is_stable(quiet_polls_seen=2, mtime=0.0, now=500.0, quiet_polls=3, min_age_s=120)
    assert not is_stable(quiet_polls_seen=3, mtime=450.0, now=500.0, quiet_polls=3, min_age_s=120)
    assert is_stable(quiet_polls_seen=3, mtime=0.0, now=500.0, quiet_polls=3, min_age_s=120)


def test_poll_once_tracks_growth_then_readiness(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    sizes = iter([(10, 0.0), (20, 1.0), (20, 1.0), (20, 1.0), (20, 1.0)])

    def stat_fn(p: Path) -> FileSnapshot:
        return snap(*next(sizes))

    state = WatchState()
    ready: list[Path] = []
    for now in (100.0, 200.0, 300.0, 400.0, 500.0):
        state, ready = poll_once(
            tmp_path,
            state,
            now=now,
            stat_fn=stat_fn,
            is_known=lambda p: False,
            quiet_polls=3,
            min_age_s=120.0,
        )
    assert ready == [f]


def test_poll_once_resets_quiet_count_when_file_changes(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    state = WatchState()
    state, _ = poll_once(tmp_path, state, 0.0, lambda p: snap(1, 0.0), lambda p: False, 2, 0.0)
    state, _ = poll_once(tmp_path, state, 1.0, lambda p: snap(1, 0.0), lambda p: False, 2, 0.0)
    state, ready = poll_once(tmp_path, state, 2.0, lambda p: snap(5, 2.0), lambda p: False, 2, 0.0)
    assert ready == []
    assert state.files[f].quiet_polls_seen == 1  # first sighting of the new size


def test_poll_once_ignores_known_and_non_mp4(tmp_path: Path) -> None:
    (tmp_path / "known.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "new.MP4").write_bytes(b"x")
    state = WatchState()
    state, ready = poll_once(
        tmp_path, state, 999.0, lambda p: snap(1, 0.0), lambda p: p.name == "known.mp4", 1, 0.0
    )
    assert ready == [tmp_path / "sub" / "new.MP4"]
    assert tmp_path / "notes.txt" not in state.files


def test_poll_once_drops_vanished_files(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    state = WatchState()
    state, _ = poll_once(tmp_path, state, 0.0, lambda p: snap(1, 0.0), lambda p: False, 3, 0.0)
    f.unlink()
    state, ready = poll_once(tmp_path, state, 1.0, lambda p: snap(1, 0.0), lambda p: False, 3, 0.0)
    assert ready == [] and state.files == {}


class Recorder:
    def __init__(self, outcome: Exception | None = None) -> None:
        self.reviewed: list[Path] = []
        self.outcome = outcome

    def __call__(self, path: Path) -> None:
        self.reviewed.append(path)
        if self.outcome:
            raise self.outcome


def make_loop_args(
    tmp_path: Path, review: Recorder, probe_fail: bool = False, polls: int = 4
) -> dict[str, object]:
    slept: list[float] = []

    def sleep_fn(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= polls:
            raise StopWatching

    def probe_fn(p: Path) -> None:
        if probe_fail:
            raise VideoError("no moov")

    return {
        "directory": tmp_path,
        "review_fn": review,
        "probe_fn": probe_fn,
        "is_known": lambda p: False,
        "stat_fn": lambda p: snap(1, 0.0),
        "clock": lambda: datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        "now_fn": lambda: 1_000.0,
        "sleep_fn": sleep_fn,
        "poll_s": 1.0,
        "quiet_polls": 1,
        "min_age_s": 0.0,
    }


def test_watch_loop_reviews_stable_file_once(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    review = Recorder()
    with pytest.raises(StopWatching):
        watch_loop(**make_loop_args(tmp_path, review, polls=6))
    assert review.reviewed == [tmp_path / "a.mp4"]


def test_watch_loop_keeps_pending_when_probe_fails(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    review = Recorder()
    with pytest.raises(StopWatching):
        watch_loop(**make_loop_args(tmp_path, review, probe_fail=True, polls=6))
    assert review.reviewed == []


def test_watch_loop_continues_after_review_error(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    review = Recorder(outcome=OllamaError("HTTP 500"))
    with pytest.raises(StopWatching):
        watch_loop(**make_loop_args(tmp_path, review, polls=6))
    assert sorted(p.name for p in review.reviewed) == ["a.mp4", "b.mp4"]


def test_watch_loop_pauses_model_work_after_cap_until_next_day(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    review = Recorder(outcome=CapExceeded("cap"))
    args = make_loop_args(tmp_path, review, polls=6)
    with pytest.raises(StopWatching):
        watch_loop(**args)
    assert len(review.reviewed) == 1  # second file not attempted today
