from pathlib import Path

import pytest

from round_review.errors import HudError, VideoError
from round_review.video.probe import Recording
from round_review.vision.digits import DigitTemplates
from round_review.vision.hud import (
    HudRead,
    Region,
    build_crop_args,
    constrain_phase,
    parse_region,
    read_hud,
)
from round_review.vision.raster import Glyph

RECORDING = Recording(Path("/v/clip.mp4"), 300.0, 60.0, 1920, 1080, 10, 0.0)


def pgm(width: int, height: int, values: list[int]) -> bytes:
    return f"P5\n{width} {height}\n255\n".encode() + bytes(values)


class TestRegion:
    def test_parses_normalised_coordinates(self) -> None:
        assert parse_region("0.455,0.02,0.09,0.055") == Region(0.455, 0.02, 0.09, 0.055)

    def test_tolerates_spaces(self) -> None:
        assert parse_region(" 0.1 , 0.2 , 0.3 , 0.4 ") == Region(0.1, 0.2, 0.3, 0.4)

    @pytest.mark.parametrize("bad", ["0.1,0.2,0.3", "a,b,c,d", "", "0.1,0.2,0.3,0.4,0.5"])
    def test_rejects_malformed_regions(self, bad: str) -> None:
        with pytest.raises(HudError, match="region"):
            parse_region(bad)

    @pytest.mark.parametrize("bad", ["-0.1,0,0.5,0.5", "0,0,1.5,0.5", "0,0,0,0.5", "0.9,0,0.5,0.1"])
    def test_rejects_regions_outside_the_frame(self, bad: str) -> None:
        with pytest.raises(HudError):
            parse_region(bad)

    def test_converts_to_pixels_for_a_resolution(self) -> None:
        assert Region(0.5, 0.0, 0.1, 0.05).in_pixels(1920, 1080) == (960, 0, 192, 54)


class TestBuildCropArgs:
    def test_crops_scales_and_asks_for_gray(self) -> None:
        args = build_crop_args(
            Path("/v/a.mp4"),
            64.4,
            Region(0.455, 0.02, 0.09, 0.055),
            RECORDING,
            Path("/tmp/o.pgm"),
            4,
        )
        assert args.index("-ss") < args.index("-i")
        assert args[args.index("-ss") + 1] == "64.400"
        vf = args[args.index("-vf") + 1]
        assert "crop=172:59:873:21" in vf  # 0.09*1920, 0.055*1080, 0.455*1920, 0.02*1080
        assert "scale=688:236" in vf  # upscaled 4x so thin strokes survive thresholding
        assert "format=gray" in vf
        assert args[args.index("-frames:v") + 1] == "1"
        assert args[-1] == "/tmp/o.pgm"


class FakeFfmpeg:
    """Writes a raster containing a '1', a ':' and a '3' as solid blocks."""

    def __init__(self, payload: bytes | None = None, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail:
            raise VideoError("ffmpeg exit 1")
        out = Path(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.payload if self.payload is not None else pgm(1, 1, [0]))
        return ""


def block_raster() -> bytes:
    """Three glyphs: a 1-wide bar, a 1-wide bar, a 2-wide block, separated by blank columns."""
    rows = ["#.#.##", "#.#.##", "#.#.##", "#.#.##"]
    values = [255 if ch == "#" else 0 for row in rows for ch in row]
    return pgm(6, 4, values)


def templates_for_blocks() -> DigitTemplates:
    """Templates matching the shapes block_raster produces."""
    bar = Glyph(("#" * 6,) * 10, 0.25)
    wide = Glyph(("#" * 6,) * 10, 0.5)
    return DigitTemplates({"1": (bar,), ":": (bar,), "3": (wide,)})


class TestReadHud:
    def region(self) -> Region:
        return Region(0.4, 0.0, 0.2, 0.1)

    def test_reads_a_clock_and_reports_where_the_crop_went(self, tmp_path: Path) -> None:
        ffmpeg = FakeFfmpeg(block_raster())
        read = read_hud(
            RECORDING,
            60.0,
            self.region(),
            ffmpeg,
            DigitTemplates({}),
            out_dir=tmp_path,
            min_confidence=0.6,
        )
        assert read.crop_path is not None and read.crop_path.exists()
        assert read.glyph_count == 3
        assert len(ffmpeg.calls) == 1

    def test_without_templates_there_is_no_clock(self, tmp_path: Path) -> None:
        read = read_hud(
            RECORDING,
            60.0,
            self.region(),
            FakeFfmpeg(block_raster()),
            DigitTemplates({}),
            out_dir=tmp_path,
            min_confidence=0.6,
        )
        assert read.clock_text is None
        assert read.clock_s is None
        assert read.confidence == 0.0

    def test_an_ffmpeg_failure_is_a_read_with_an_error_not_an_exception(
        self, tmp_path: Path
    ) -> None:
        read = read_hud(
            RECORDING,
            60.0,
            self.region(),
            FakeFfmpeg(fail=True),
            DigitTemplates({}),
            out_dir=tmp_path,
            min_confidence=0.6,
        )
        assert read.error is not None and "ffmpeg" in read.error
        assert read.clock_s is None

    def test_an_empty_crop_reads_nothing(self, tmp_path: Path) -> None:
        blank = pgm(4, 4, [0] * 16)
        read = read_hud(
            RECORDING,
            60.0,
            self.region(),
            FakeFfmpeg(blank),
            DigitTemplates({}),
            out_dir=tmp_path,
            min_confidence=0.6,
        )
        assert read.glyph_count == 0
        assert read.clock_s is None


class TestConstrainPhase:
    def live(self, seconds: float = 99.0) -> HudRead:
        return HudRead(clock_text="1:39", clock_s=seconds, confidence=0.95, glyph_count=4)

    def unreadable(self) -> HudRead:
        return HudRead(clock_text=None, clock_s=None, confidence=0.0, glyph_count=0)

    def test_a_long_clock_vetoes_buy_phase(self) -> None:
        verdict = constrain_phase(
            "pre_round", self.live(), buy_phase_max_s=45.0, min_confidence=0.8
        )
        assert verdict.phase in ("early", "mid")
        assert verdict.overridden is True
        assert "1:39" in (verdict.reason or "")
        assert "buy phase" in (verdict.reason or "").lower()

    def test_a_long_clock_vetoes_post_plant_and_retake(self) -> None:
        # the spike timer never exceeds 45s, so a 1:39 clock cannot be post-plant
        for claimed in ("post_plant", "retake"):
            verdict = constrain_phase(claimed, self.live(), 45.0, 0.8)
            assert verdict.overridden is True
            assert verdict.phase in ("early", "mid")

    def test_a_long_clock_rescues_an_unreadable_phase(self) -> None:
        verdict = constrain_phase(None, self.live(), 45.0, 0.8)
        assert verdict.phase in ("early", "mid")
        assert verdict.overridden is True

    def test_early_and_mid_are_split_on_the_clock(self) -> None:
        assert constrain_phase(None, self.live(95.0), 45.0, 0.8).phase == "early"
        assert constrain_phase(None, self.live(60.0), 45.0, 0.8).phase == "mid"

    def test_spectating_is_never_overridden(self) -> None:
        # the timer belongs to whoever is being watched, so it says nothing about the player
        verdict = constrain_phase("spectating", self.live(), 45.0, 0.8)
        assert verdict.overridden is False
        assert verdict.phase == "spectating"

    def test_a_short_clock_changes_nothing(self) -> None:
        # 0:30 is consistent with buy phase, mid round and post-plant alike
        short = HudRead("0:30", 30.0, 0.95, 4)
        assert constrain_phase("pre_round", short, 45.0, 0.8).overridden is False
        assert constrain_phase("pre_round", short, 45.0, 0.8).phase == "pre_round"

    def test_a_live_phase_is_left_alone(self) -> None:
        verdict = constrain_phase("mid", self.live(), 45.0, 0.8)
        assert verdict.overridden is False
        assert verdict.phase == "mid"

    def test_an_unreadable_hud_changes_nothing(self) -> None:
        verdict = constrain_phase("pre_round", self.unreadable(), 45.0, 0.8)
        assert verdict.overridden is False
        assert verdict.phase == "pre_round"

    def test_a_low_confidence_read_is_not_trusted(self) -> None:
        shaky = HudRead("1:39", 99.0, 0.5, 4)
        assert constrain_phase("pre_round", shaky, 45.0, 0.8).overridden is False

    def test_no_hud_read_at_all_changes_nothing(self) -> None:
        verdict = constrain_phase("pre_round", None, 45.0, 0.8)
        assert verdict.overridden is False
        assert verdict.phase == "pre_round"
