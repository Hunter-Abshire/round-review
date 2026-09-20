"""Polling folder watcher. Polling (not inotify/ReadDirectoryChanges) because recorders write
MP4s progressively and we only care whether a file has stopped changing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from round_review.errors import CapExceeded, RoundReviewError, VideoError

log = logging.getLogger(__name__)

VIDEO_SUFFIXES: frozenset[str] = frozenset({".mp4"})


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    size_bytes: int
    mtime: float


@dataclass(frozen=True, slots=True)
class Tracked:
    snapshot: FileSnapshot
    quiet_polls_seen: int


@dataclass(slots=True)
class WatchState:
    files: dict[Path, Tracked] = field(default_factory=dict)
    paused_on: date | None = None


StatFn = Callable[[Path], FileSnapshot]
KnownFn = Callable[[Path], bool]


def stat_snapshot(path: Path) -> FileSnapshot:
    st = path.stat()
    return FileSnapshot(size_bytes=st.st_size, mtime=st.st_mtime)


def is_stable(
    quiet_polls_seen: int, mtime: float, now: float, quiet_polls: int, min_age_s: float
) -> bool:
    """A file is stable once it has been seen unchanged for `quiet_polls` polls and its
    mtime is at least `min_age_s` old. `quiet_polls_seen` counts the first sighting as 1."""
    return quiet_polls_seen >= quiet_polls and (now - mtime) >= min_age_s


def list_candidates(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def poll_once(
    directory: Path,
    state: WatchState,
    now: float,
    stat_fn: StatFn,
    is_known: KnownFn,
    quiet_polls: int,
    min_age_s: float,
) -> tuple[WatchState, list[Path]]:
    """One pass over the directory. Returns the new state and the files that are stable
    this poll. Files that vanished are dropped; files already known are ignored."""
    files: dict[Path, Tracked] = {}
    ready: list[Path] = []
    for path in list_candidates(directory):
        if is_known(path):
            continue
        try:
            snapshot = stat_fn(path)
        except OSError as exc:
            log.debug("%s: stat failed, skipping this poll: %s", path, exc)
            continue
        previous = state.files.get(path)
        quiet = previous.quiet_polls_seen + 1 if previous and previous.snapshot == snapshot else 1
        files[path] = Tracked(snapshot, quiet)
        if is_stable(quiet, snapshot.mtime, now, quiet_polls, min_age_s):
            ready.append(path)
    return WatchState(files=files, paused_on=state.paused_on), ready


def watch_loop(
    directory: Path,
    review_fn: Callable[[Path], object],
    probe_fn: Callable[[Path], object],
    is_known: KnownFn,
    stat_fn: StatFn,
    clock: Callable[[], datetime],
    now_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
    poll_s: float,
    quiet_polls: int,
    min_age_s: float,
) -> None:
    """Run forever. `sleep_fn` is the only exit: raise from it (or KeyboardInterrupt) to stop.

    Per-file errors are logged and the loop continues. `CapExceeded` pauses model work until
    the calendar day changes. A file whose probe fails stays pending (it is probably still being
    written). A file is attempted at most once per session, regardless of outcome, except when
    the cap stopped it."""
    state = WatchState()
    attempted: set[Path] = set()
    log.info("watching %s every %.0fs", directory, poll_s)
    while True:
        today = clock().date()
        if state.paused_on == today:
            log.info("daily model-call cap reached; pausing until tomorrow")
            sleep_fn(poll_s)
            continue
        state, ready = poll_once(
            directory, state, now_fn(), stat_fn, is_known, quiet_polls, min_age_s
        )
        for path in ready:
            if path in attempted:
                continue
            try:
                probe_fn(path)
            except VideoError as exc:
                log.info("%s: not readable yet (%s); will retry", path.name, exc)
                continue
            try:
                review_fn(path)
            except CapExceeded as exc:
                log.warning("%s: %s", path.name, exc)
                state.paused_on = today
                break
            except RoundReviewError as exc:
                log.error("%s: %s: %s", path.name, type(exc).__name__, exc)
            finally:
                if state.paused_on != today:
                    attempted.add(path)
        sleep_fn(poll_s)
