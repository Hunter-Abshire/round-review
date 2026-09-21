"""End to end: a real video file, a real ffmpeg crop, learned templates, a clock read.

Nothing here touches a model. If this passes, the deterministic HUD path works; what it
cannot prove is that the default region matches a real Valorant recording, which is what
`round-review hud crop` is for.
"""

from pathlib import Path

import pytest

from round_review.errors import HudError
from round_review.video.probe import SubprocessRunner, probe
from round_review.vision.digits import DigitTemplates
from round_review.vision.hud import Region, learn_from_crop, read_hud
from tests.conftest import requires_ffmpeg

# Seven-segment strokes, so the test draws its own font rather than depending on a system one.
SEGMENTS: dict[str, str] = {
    "0": "abcdef",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}
FRAME_W, FRAME_H = 640, 360
# Where the clock is drawn, and therefore the region the reader is pointed at.
CLOCK_REGION = Region(0.40, 0.03, 0.20, 0.12)


def _draw_segment(px: bytearray, name: str, x: int, y: int, w: int, h: int, t: int) -> None:
    def fill(x0: int, y0: int, x1: int, y1: int) -> None:
        for yy in range(max(0, y0), min(FRAME_H, y1)):
            for xx in range(max(0, x0), min(FRAME_W, x1)):
                px[yy * FRAME_W + xx] = 255

    mid = y + h // 2
    if name == "a":
        fill(x, y, x + w, y + t)
    elif name == "b":
        fill(x + w - t, y, x + w, mid)
    elif name == "c":
        fill(x + w - t, mid, x + w, y + h)
    elif name == "d":
        fill(x, y + h - t, x + w, y + h)
    elif name == "e":
        fill(x, mid, x + t, y + h)
    elif name == "f":
        fill(x, y, x + t, mid)
    elif name == "g":
        fill(x, mid - t // 2, x + w, mid + t // 2)


def render_clock_frame(text: str) -> bytes:
    """A dark frame with `text` drawn as bright seven-segment glyphs inside CLOCK_REGION."""
    px = bytearray(FRAME_W * FRAME_H)
    x0, y0, _w, h = CLOCK_REGION.in_pixels(FRAME_W, FRAME_H)
    digit_w, gap, thickness = 14, 8, 4
    colon_w = 6
    cursor = x0 + 4
    for char in text:
        if char == ":":
            for dy in (h // 3, 2 * h // 3):
                for yy in range(y0 + dy - 3, y0 + dy + 3):
                    for xx in range(cursor, cursor + colon_w):
                        px[yy * FRAME_W + xx] = 255
            cursor += colon_w + gap
            continue
        for segment in SEGMENTS[char]:
            _draw_segment(px, segment, cursor, y0 + 6, digit_w, h - 12, thickness)
        cursor += digit_w + gap
    return f"P5\n{FRAME_W} {FRAME_H}\n255\n".encode() + bytes(px)


@pytest.fixture(scope="module")
def clock_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import subprocess

    work = tmp_path_factory.mktemp("clock")
    frame = work / "frame.pgm"
    frame.write_bytes(render_clock_frame("1:39"))
    out = work / "clock.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame),
            "-t",
            "2",
            "-r",
            "10",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


@requires_ffmpeg
@pytest.mark.integration
class TestRealVideo:
    def read(self, video: Path, templates: DigitTemplates, tmp_path: Path):  # type: ignore[no-untyped-def]
        recording = probe(video, SubprocessRunner("ffprobe"))
        return read_hud(
            recording,
            1.0,
            CLOCK_REGION,
            SubprocessRunner("ffmpeg"),
            templates,
            out_dir=tmp_path,
            min_confidence=0.7,
        )

    def test_finds_the_right_number_of_glyphs(self, clock_video: Path, tmp_path: Path) -> None:
        read = self.read(clock_video, DigitTemplates({}), tmp_path)
        assert read.error is None
        assert read.glyph_count == 4  # 1, :, 3, 9

    def test_learns_the_digits_and_reads_the_clock_back(
        self, clock_video: Path, tmp_path: Path
    ) -> None:
        recording = probe(clock_video, SubprocessRunner("ffprobe"))
        samples = learn_from_crop(
            recording, 1.0, CLOCK_REGION, SubprocessRunner("ffmpeg"), tmp_path / "learn", "1:39"
        )
        templates = DigitTemplates({}).learn(samples)  # type: ignore[arg-type]
        assert templates.characters() == {"1", ":", "3", "9"}

        read = self.read(clock_video, templates, tmp_path / "read")
        assert read.clock_text == "1:39"
        assert read.clock_s == 99.0
        assert read.confidence > 0.9

    def test_learning_with_the_wrong_text_says_so(self, clock_video: Path, tmp_path: Path) -> None:
        recording = probe(clock_video, SubprocessRunner("ffprobe"))
        with pytest.raises(HudError, match="4 glyph"):
            learn_from_crop(
                recording, 1.0, CLOCK_REGION, SubprocessRunner("ffmpeg"), tmp_path, "12:34"
            )

    def test_a_learned_clock_vetoes_a_buy_phase_misread(
        self, clock_video: Path, tmp_path: Path
    ) -> None:
        from round_review.vision.hud import constrain_phase

        recording = probe(clock_video, SubprocessRunner("ffprobe"))
        samples = learn_from_crop(
            recording, 1.0, CLOCK_REGION, SubprocessRunner("ffmpeg"), tmp_path / "l", "1:39"
        )
        read = self.read(clock_video, DigitTemplates({}).learn(samples), tmp_path / "r")  # type: ignore[arg-type]
        verdict = constrain_phase("pre_round", read)
        assert verdict.overridden is True
        assert verdict.phase == "early"
