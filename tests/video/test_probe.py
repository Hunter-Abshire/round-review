import json
from pathlib import Path

import pytest

from round_review.errors import VideoError
from round_review.video.probe import Recording, SubprocessRunner, parse_probe_json, probe
from tests.conftest import requires_ffmpeg

PROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "60/1",
            },
        ],
        "format": {"duration": "123.456", "size": "98765"},
    }
)


class FakeRunner:
    def __init__(self, output: str = PROBE_JSON, fail: Exception | None = None) -> None:
        self.output = output
        self.fail = fail
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail:
            raise self.fail
        return self.output


def test_parse_probe_json() -> None:
    rec = parse_probe_json(PROBE_JSON, Path("/v/a.mp4"), mtime=5.0)
    assert rec == Recording(
        path=Path("/v/a.mp4"),
        duration_s=123.456,
        fps=60.0,
        width=1920,
        height=1080,
        size_bytes=98765,
        mtime=5.0,
    )


def test_parse_fractional_frame_rate() -> None:
    text = PROBE_JSON.replace("60/1", "30000/1001")
    rec = parse_probe_json(text, Path("/v/a.mp4"), mtime=0.0)
    assert rec.fps == pytest.approx(29.97, abs=0.01)


def test_missing_video_stream_is_video_error() -> None:
    text = json.dumps({"streams": [], "format": {"duration": "1"}})
    with pytest.raises(VideoError, match="no video stream"):
        parse_probe_json(text, Path("/v/a.mp4"), mtime=0.0)


def test_missing_duration_is_video_error() -> None:
    text = json.dumps({"streams": [{"codec_type": "video", "width": 1, "height": 1}], "format": {}})
    with pytest.raises(VideoError, match="duration"):
        parse_probe_json(text, Path("/v/a.mp4"), mtime=0.0)


def test_garbage_output_is_video_error() -> None:
    with pytest.raises(VideoError):
        parse_probe_json("not json", Path("/v/a.mp4"), mtime=0.0)


def test_probe_builds_argv_and_uses_stat(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 10)
    runner = FakeRunner()
    rec = probe(video, runner)
    assert runner.calls[0][-1] == str(video)
    assert "-print_format" in runner.calls[0] or "-of" in runner.calls[0]
    assert rec.mtime == video.stat().st_mtime
    assert rec.duration_s == 123.456


def test_probe_runner_failure_is_video_error(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    runner = FakeRunner(fail=VideoError("ffprobe exit 1"))
    with pytest.raises(VideoError, match="exit 1"):
        probe(video, runner)


def test_subprocess_runner_missing_binary_is_video_error() -> None:
    runner = SubprocessRunner(executable="definitely-not-a-binary-xyz")
    with pytest.raises(VideoError, match="not found"):
        runner.run(["-version"])


def test_subprocess_runner_nonzero_exit_is_video_error() -> None:
    runner = SubprocessRunner(executable="false")
    with pytest.raises(VideoError, match="exit"):
        runner.run([])


@requires_ffmpeg
@pytest.mark.integration
def test_probe_real_video(sample_video: Path) -> None:
    rec = probe(sample_video, SubprocessRunner(executable="ffprobe"))
    assert rec.duration_s == pytest.approx(5.0, abs=0.1)
    assert rec.width == 640
    assert rec.height == 360
    assert rec.fps == pytest.approx(30.0)
    assert rec.size_bytes == sample_video.stat().st_size
