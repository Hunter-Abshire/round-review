"""ffmpeg frame extraction for one window."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from round_review.errors import VideoError
from round_review.video.probe import CommandRunner, Recording
from round_review.video.windows import Window


@dataclass(frozen=True, slots=True)
class FrameSample:
    window_index: int
    timestamp_s: float
    path: Path


def _pattern(window: Window, out_dir: Path) -> Path:
    return out_dir / f"w{window.index:02d}_%03d.jpg"


def build_extract_args(
    path: Path, window: Window, fps: float, width: int, out_dir: Path
) -> list[str]:
    """ffmpeg argv. `-ss` before `-i` is a fast keyframe seek; accuracy is within one GOP."""
    return [
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{window.start_s:.3f}",
        "-t",
        f"{window.end_s - window.start_s:.3f}",
        "-i",
        str(path),
        "-vf",
        f"fps={fps},scale={width}:-2",
        "-q:v",
        "4",
        str(_pattern(window, out_dir)),
    ]


def extract_frames(
    recording: Recording,
    window: Window,
    runner: CommandRunner,
    fps: float,
    width: int,
    out_dir: Path,
) -> list[FrameSample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    runner.run(build_extract_args(recording.path, window, fps, width, out_dir))
    files = sorted(out_dir.glob(f"w{window.index:02d}_*.jpg"))
    if not files:
        raise VideoError(f"{recording.path}: ffmpeg produced no frames for window {window.index}")
    return [FrameSample(window.index, window.start_s + i / fps, f) for i, f in enumerate(files)]


def encode_frame_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_single_frame_args(
    path: Path, timestamp_s: float, width: int, out_path: Path
) -> list[str]:
    """One frame at an exact timestamp. `-ss` before `-i` is accurate for output since ffmpeg 2.1
    (it decodes from the previous keyframe and discards), so the picture matches the finding."""
    return [
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "3",
        str(out_path),
    ]


def extract_single_frame(
    recording: Recording, timestamp_s: float, runner: CommandRunner, width: int, out_path: Path
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    runner.run(build_single_frame_args(recording.path, timestamp_s, width, out_path))
    if not out_path.exists():
        raise VideoError(f"{recording.path}: ffmpeg produced no frame at t={timestamp_s:.1f}s")
    return out_path
