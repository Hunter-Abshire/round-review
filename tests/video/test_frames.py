from pathlib import Path

import pytest

from round_review.errors import VideoError
from round_review.video.frames import (
    FrameSample,
    build_extract_args,
    encode_frame_b64,
    extract_frames,
)
from round_review.video.probe import Recording, SubprocessRunner
from round_review.video.windows import Window
from tests.conftest import requires_ffmpeg


class FakeFfmpeg:
    def __init__(self, files_to_create: int = 0) -> None:
        self.files_to_create = files_to_create
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        pattern = Path(args[-1])
        for i in range(1, self.files_to_create + 1):
            (pattern.parent / (pattern.name % i)).write_bytes(b"jpeg")
        return ""


def rec(path: Path = Path("/v/a.mp4")) -> Recording:
    return Recording(path, 100.0, 30.0, 1920, 1080, 1, 0.0)


def test_build_extract_args_shape(tmp_path: Path) -> None:
    window = Window(index=1, start_s=40.0, end_s=52.0, source="evenly_spaced")
    args = build_extract_args(Path("/v/a.mp4"), window, fps=1.0, width=1280, out_dir=tmp_path)
    # fast seek: -ss before -i
    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-ss") + 1] == "40.000"
    assert args[args.index("-t") + 1] == "12.000"
    assert args[args.index("-i") + 1] == str(Path("/v/a.mp4"))
    vf = args[args.index("-vf") + 1]
    assert "fps=1.0" in vf
    assert "scale=1280:-2" in vf
    assert args[-1] == str(tmp_path / "w01_%03d.jpg")
    assert "-y" in args


def test_extract_frames_returns_ordered_samples_with_timestamps(tmp_path: Path) -> None:
    window = Window(index=0, start_s=30.0, end_s=42.0, source="evenly_spaced")
    ffmpeg = FakeFfmpeg(files_to_create=12)
    samples = extract_frames(rec(), window, ffmpeg, fps=1.0, width=1280, out_dir=tmp_path)
    assert len(samples) == 12
    assert samples[0] == FrameSample(
        window_index=0, timestamp_s=30.0, path=tmp_path / "w00_001.jpg"
    )
    assert samples[-1].timestamp_s == pytest.approx(41.0)
    assert [s.path.name for s in samples] == sorted(s.path.name for s in samples)


def test_extract_frames_half_fps_timestamps(tmp_path: Path) -> None:
    window = Window(index=2, start_s=10.0, end_s=22.0, source="evenly_spaced")
    samples = extract_frames(rec(), window, FakeFfmpeg(6), fps=0.5, width=640, out_dir=tmp_path)
    assert [s.timestamp_s for s in samples] == pytest.approx([10, 12, 14, 16, 18, 20])


def test_no_frames_produced_is_video_error(tmp_path: Path) -> None:
    window = Window(index=0, start_s=0.0, end_s=12.0, source="evenly_spaced")
    with pytest.raises(VideoError, match="no frames"):
        extract_frames(rec(), window, FakeFfmpeg(0), fps=1.0, width=1280, out_dir=tmp_path)


def test_encode_frame_b64(tmp_path: Path) -> None:
    f = tmp_path / "x.jpg"
    f.write_bytes(b"hello")
    assert encode_frame_b64(f) == "aGVsbG8="


@requires_ffmpeg
@pytest.mark.integration
def test_extract_frames_real_video(sample_video: Path, tmp_path: Path) -> None:
    window = Window(index=0, start_s=1.0, end_s=4.0, source="evenly_spaced")
    ffmpeg = SubprocessRunner(executable="ffmpeg")
    samples = extract_frames(
        rec(sample_video), window, ffmpeg, fps=1.0, width=320, out_dir=tmp_path
    )
    assert len(samples) == 3
    assert all(s.path.exists() and s.path.stat().st_size > 0 for s in samples)
    assert samples[0].path.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
