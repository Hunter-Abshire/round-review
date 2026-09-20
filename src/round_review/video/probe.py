"""ffprobe wrapper: turn a file on disk into a Recording."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from round_review.errors import VideoError


@dataclass(frozen=True, slots=True)
class Recording:
    path: Path
    duration_s: float
    fps: float
    width: int
    height: int
    size_bytes: int
    mtime: float


class CommandRunner(Protocol):
    """Runs an external binary with the given arguments and returns its stdout."""

    def run(self, args: list[str]) -> str: ...


@dataclass(frozen=True, slots=True)
class SubprocessRunner:
    executable: str

    def run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [self.executable, *args], capture_output=True, text=True, check=False
            )
        except FileNotFoundError as exc:
            raise VideoError(f"{self.executable}: binary not found on PATH") from exc
        if result.returncode != 0:
            raise VideoError(
                f"{self.executable} exit {result.returncode}: {result.stderr.strip()[-500:]}"
            )
        return result.stdout


def _parse_rate(text: str) -> float:
    num, _, den = text.partition("/")
    denominator = float(den) if den else 1.0
    if denominator == 0:
        raise ValueError(f"zero denominator in frame rate {text!r}")
    return float(num) / denominator


def parse_probe_json(text: str, path: Path, mtime: float) -> Recording:
    try:
        payload = json.loads(text)
        video = next(s for s in payload.get("streams", []) if s.get("codec_type") == "video")
    except json.JSONDecodeError as exc:
        raise VideoError(f"{path}: ffprobe output is not JSON: {exc}") from exc
    except StopIteration:
        raise VideoError(f"{path}: no video stream") from None
    fmt = payload.get("format", {})
    try:
        duration = float(fmt["duration"])
        return Recording(
            path=path,
            duration_s=duration,
            fps=_parse_rate(str(video.get("r_frame_rate", "0/1"))),
            width=int(video["width"]),
            height=int(video["height"]),
            size_bytes=int(fmt.get("size", 0)),
            mtime=mtime,
        )
    except KeyError as exc:
        raise VideoError(f"{path}: ffprobe output missing {exc}") from exc
    except ValueError as exc:
        raise VideoError(f"{path}: ffprobe output malformed: {exc}") from exc


def probe(path: Path, runner: CommandRunner) -> Recording:
    """Probe `path` with ffprobe. A file still being written usually fails here."""
    args = [
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    output = runner.run(args)
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise VideoError(f"{path}: cannot stat: {exc}") from exc
    return parse_probe_json(output, path, mtime)
