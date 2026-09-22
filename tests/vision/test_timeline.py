"""Rounds are found by watching the clock, not by asking the model."""

from pathlib import Path

from round_review.video.probe import Recording
from round_review.vision.hud import Region
from round_review.vision.timeline import (
    ClockSample,
    build_scan_args,
    round_at,
    scan_timestamps,
    segment_rounds,
)

RECORDING = Recording(Path("/v/clip.mp4"), 600.0, 60.0, 1920, 1080, 10, 0.0)
REGION = Region(0.455, 0.02, 0.09, 0.055)


def samples(pairs: list[tuple[float, float | None]]) -> tuple[ClockSample, ...]:
    return tuple(ClockSample(t, c, 1.0 if c is not None else 0.0) for t, c in pairs)


class TestScanArgs:
    def test_one_pass_writes_numbered_crops(self) -> None:
        args = build_scan_args(REGION, RECORDING, Path("/out"), interval_s=2.0)
        assert args[args.index("-i") + 1] == "/v/clip.mp4"
        chain = args[args.index("-vf") + 1]
        # fps first, so the crop and scale only run on the frames that are kept.
        assert chain.startswith("fps=1/2.0,crop=172:59:873:21")
        assert chain.endswith("format=gray")
        assert args[-1] == str(Path("/out") / "clock_%05d.pgm")

    def test_the_stem_names_the_output_so_two_scans_can_share_a_directory(self) -> None:
        args = build_scan_args(REGION, RECORDING, Path("/out"), interval_s=2.0, stem="health")
        assert args[-1] == str(Path("/out") / "health_%05d.pgm")

    def test_frame_numbers_map_back_to_seconds(self) -> None:
        assert scan_timestamps(3, interval_s=2.0) == (0.0, 2.0, 4.0)


class TestSegmentRounds:
    def test_a_reset_to_the_round_timer_starts_a_round(self) -> None:
        # buy phase counting down, barrier drops to 1:40, then a second round.
        spans = segment_rounds(
            samples([(0.0, 20.0), (2.0, 10.0), (4.0, 100.0), (6.0, 80.0), (8.0, 100.0)])
        )
        assert [(s.index, s.start_s, s.end_s) for s in spans] == [
            (1, 0.0, 4.0),
            (2, 4.0, 8.0),
            (3, 8.0, 8.0),
        ]

    def test_a_countdown_alone_is_one_round(self) -> None:
        spans = segment_rounds(samples([(0.0, 100.0), (2.0, 90.0), (4.0, 80.0)]))
        assert len(spans) == 1
        assert spans[0].start_s == 0.0 and spans[0].end_s == 4.0

    def test_the_spike_timer_is_not_a_new_round(self) -> None:
        # 0:45 is the spike, well under the round timer, so it must not split the round.
        spans = segment_rounds(samples([(0.0, 100.0), (2.0, 20.0), (4.0, 45.0), (6.0, 30.0)]))
        assert len(spans) == 1

    def test_the_buy_phase_is_not_a_new_round(self) -> None:
        spans = segment_rounds(samples([(0.0, 100.0), (2.0, 5.0), (4.0, 30.0), (6.0, 20.0)]))
        assert len(spans) == 1

    def test_unreadable_samples_do_not_break_the_chain(self) -> None:
        spans = segment_rounds(
            samples([(0.0, 100.0), (2.0, None), (4.0, 80.0), (6.0, None), (8.0, 100.0)])
        )
        assert len(spans) == 2
        assert spans[1].start_s == 8.0

    def test_no_readable_samples_means_no_rounds(self) -> None:
        assert segment_rounds(samples([(0.0, None), (2.0, None)])) == ()

    def test_low_confidence_samples_are_ignored(self) -> None:
        raw = (
            ClockSample(0.0, 100.0, 1.0),
            ClockSample(2.0, 100.0, 0.2),
            ClockSample(4.0, 90.0, 1.0),
        )
        assert len(segment_rounds(raw, min_confidence=0.8)) == 1


class TestRoundAt:
    def test_finds_the_round_covering_a_timestamp(self) -> None:
        spans = segment_rounds(
            samples([(0.0, 100.0), (10.0, 20.0), (20.0, 100.0), (30.0, 20.0), (40.0, 100.0)])
        )
        first, second = round_at(spans, 5.0), round_at(spans, 25.0)
        assert first is not None and first.index == 1
        assert second is not None and second.index == 2

    def test_a_timestamp_before_the_first_round_has_none(self) -> None:
        spans = segment_rounds(samples([(10.0, 100.0), (20.0, 20.0), (30.0, 100.0)]))
        assert round_at(spans, 1.0) is None

    def test_no_rounds_means_none(self) -> None:
        assert round_at((), 5.0) is None


class TestLiveEnd:
    """A round span runs barrier drop to barrier drop, so its tail is the NEXT round's buy
    phase. Anchoring the round-ending window there reviewed people shopping."""

    def test_the_trailing_buy_phase_is_not_part_of_live_play(self) -> None:
        # round timer counting down, then the next buy phase counting 0:30 -> 0:00
        spans = segment_rounds(
            samples(
                [
                    (0.0, 100.0),
                    (2.0, 80.0),
                    (4.0, 40.0),
                    (6.0, 30.0),
                    (8.0, 20.0),
                    (10.0, 10.0),
                    (12.0, 100.0),
                ]
            )
        )
        # live play ended when the buy phase began, at t=6
        assert spans[0].live_end_s == 6.0

    def test_a_round_with_no_trailing_buy_phase_ends_where_it_ends(self) -> None:
        spans = segment_rounds(samples([(0.0, 100.0), (2.0, 90.0), (4.0, 80.0)]))
        assert spans[0].live_end_s == spans[0].end_s

    def test_unreadable_post_plant_samples_still_count_as_live(self) -> None:
        # the spike icon replaces the timer, so post-plant reads as nothing at all
        spans = segment_rounds(
            samples(
                [
                    (0.0, 100.0),
                    (2.0, 60.0),
                    (4.0, None),
                    (6.0, None),
                    (8.0, 25.0),
                    (10.0, 5.0),
                    (12.0, 100.0),
                ]
            )
        )
        assert spans[0].live_end_s == 8.0

    def test_the_round_timer_passing_through_forty_seconds_is_not_a_buy_phase(self) -> None:
        spans = segment_rounds(
            samples(
                [(0.0, 100.0), (2.0, 80.0), (4.0, 40.0), (6.0, 25.0), (8.0, 10.0), (10.0, 100.0)]
            )
        )
        # 0:40 is above the 0:30 the buy phase starts from, so live play ran until t=6
        assert spans[0].live_end_s == 6.0


def test_a_buy_phase_that_only_reads_intermittently_is_still_found() -> None:
    """Measured on real footage: the clock is legible in about half the buy-phase frames
    while the shop is open, so a single unreadable sample must not end the walk back."""
    got = segment_rounds(
        samples(
            [
                (0.0, 100.0),
                (2.0, 60.0),
                (4.0, None),
                (6.0, None),
                (8.0, 26.0),
                (10.0, None),
                (12.0, 18.0),
                (14.0, None),
                (16.0, 2.0),
                (18.0, 100.0),
            ]
        )
    )
    assert got[0].live_end_s == 8.0
