"""Append-only JSONL ledger of processed recordings and model-call counts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, get_args

from round_review.errors import LedgerError

Status = Literal["ok", "partial", "failed", "skipped"]
STATUSES: tuple[str, ...] = get_args(Status)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    key: str
    path: str
    processed_at: datetime
    model_calls: int
    report_path: str | None
    status: Status
    error: str | None
    # How much work the review did and how long it took, so the next one can be estimated.
    windows: int = 0
    duration_s: float = 0.0

    def seconds_per_window(self) -> float | None:
        if self.windows <= 0 or self.duration_s <= 0:
            return None
        return self.duration_s / self.windows


def recording_key(path: Path, size_bytes: int, mtime: float) -> str:
    """Stable identity for a recording: name + size + mtime, hashed to 16 hex chars."""
    raw = f"{path.name}|{size_bytes}|{mtime:.3f}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _to_json(entry: LedgerEntry) -> str:
    payload = asdict(entry)
    payload["processed_at"] = entry.processed_at.isoformat()
    return json.dumps(payload, separators=(",", ":"))


def _from_json(line: str, lineno: int, path: Path) -> LedgerEntry:
    try:
        payload = json.loads(line)
        status = payload["status"]
        if status not in STATUSES:
            raise ValueError(f"invalid status {status!r}")
        return LedgerEntry(
            key=str(payload["key"]),
            path=str(payload["path"]),
            processed_at=datetime.fromisoformat(payload["processed_at"]),
            model_calls=int(payload["model_calls"]),
            report_path=payload.get("report_path"),
            status=status,
            error=payload.get("error"),
            windows=int(payload.get("windows", 0)),
            duration_s=float(payload.get("duration_s", 0.0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise LedgerError(f"{path}: line {lineno}: {exc}") from exc


def read_ledger(path: Path) -> list[LedgerEntry]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LedgerError(f"{path}: cannot read ledger: {exc}") from exc
    return [_from_json(line, i, path) for i, line in enumerate(lines, start=1) if line.strip()]


def append_entry(path: Path, entry: LedgerEntry) -> None:
    if entry.status not in STATUSES:
        raise LedgerError(f"invalid status {entry.status!r}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_to_json(entry) + "\n")
    except OSError as exc:
        raise LedgerError(f"{path}: cannot append to ledger: {exc}") from exc


def is_processed(entries: list[LedgerEntry], key: str) -> bool:
    return any(e.key == key for e in entries)


def calls_today(entries: list[LedgerEntry], today: date) -> int:
    return sum(e.model_calls for e in entries if e.processed_at.date() == today)
