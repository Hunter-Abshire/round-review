from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from round_review.errors import LedgerError
from round_review.ledger import (
    LedgerEntry,
    append_entry,
    calls_today,
    is_processed,
    read_ledger,
    recording_key,
)


def make_entry(key: str = "k1", when: datetime | None = None, calls: int = 3) -> LedgerEntry:
    return LedgerEntry(
        key=key,
        path=f"/vids/{key}.mp4",
        processed_at=when or datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
        model_calls=calls,
        report_path="/reports/r.md",
        status="ok",
        error=None,
    )


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_ledger(tmp_path / "ledger.jsonl") == []


def test_append_then_read_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    e1 = make_entry("a")
    e2 = LedgerEntry(
        key="b",
        path="/vids/b.mp4",
        processed_at=datetime(2026, 9, 20, 11, 0, tzinfo=UTC),
        model_calls=0,
        report_path=None,
        status="failed",
        error="VideoError: ffprobe exit 1",
    )
    append_entry(path, e1)
    append_entry(path, e2)
    assert read_ledger(path) == [e1, e2]


def test_append_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "ledger.jsonl"
    append_entry(path, make_entry())
    assert len(read_ledger(path)) == 1


def test_is_processed() -> None:
    entries = [make_entry("a"), make_entry("b")]
    assert is_processed(entries, "a")
    assert not is_processed(entries, "zzz")


def test_calls_today_respects_day_boundary() -> None:
    entries = [
        make_entry("a", datetime(2026, 9, 19, 23, 59, tzinfo=UTC), calls=4),
        make_entry("b", datetime(2026, 9, 20, 0, 1, tzinfo=UTC), calls=2),
        make_entry("c", datetime(2026, 9, 20, 12, 0, tzinfo=UTC), calls=5),
    ]
    assert calls_today(entries, date(2026, 9, 20)) == 7
    assert calls_today(entries, date(2026, 9, 19)) == 4
    assert calls_today(entries, date(2026, 9, 21)) == 0


def test_corrupt_line_is_ledger_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_entry(path, make_entry())
    with path.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(LedgerError, match="line 2"):
        read_ledger(path)


def test_missing_field_is_ledger_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"key": "a"}\n')
    with pytest.raises(LedgerError, match="line 1"):
        read_ledger(path)


def test_invalid_status_is_ledger_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(LedgerError):
        append_entry(path, LedgerEntry("k", "p", datetime.now(UTC), 0, None, "weird", None))  # type: ignore[arg-type]


def test_recording_key_is_stable_and_distinct() -> None:
    k1 = recording_key(Path("/vids/a.mp4"), size_bytes=100, mtime=1.0)
    k2 = recording_key(Path("/vids/a.mp4"), size_bytes=100, mtime=1.0)
    k3 = recording_key(Path("/vids/a.mp4"), size_bytes=101, mtime=1.0)
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16
