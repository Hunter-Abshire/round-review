import json
from collections import deque
from pathlib import Path

import pytest

from round_review.coaching.situation import Situation
from round_review.errors import LabelsError, OllamaError
from round_review.llm.transport import ChatRequest, ChatResponse
from round_review.validation.scenes import (
    SceneCase,
    SceneReport,
    frame_offsets,
    load_cases,
    render_scene_report,
    run_cases,
    scaffold_cases,
    scene_accuracy,
    write_cases,
)
from round_review.video.probe import Recording


class FakeTransport:
    def __init__(self, *contents: str | Exception) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        item = self.responses.popleft()
        if isinstance(item, Exception):
            raise item
        return ChatResponse(str(item), 0, 0)


class FakeFfmpeg:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        out = Path(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\xff\xd8jpeg")
        return ""


def situation_json(phase: str = "early", **overrides: object) -> str:
    payload: dict[str, object] = {
        "agent": "Jett",
        "map": "Ascent",
        "side": "attack",
        "phase": phase,
        "weapon": "Vandal",
        "abilities_available": ["Tailwind"],
        "credits": 3900,
        "teammates_alive": 4,
        "enemies_visible": 1,
        "timeline": [{"t": 10.0, "event": "holding an angle"}],
        "summary": "Live round, holding A main.",
    }
    payload.update(overrides)
    return json.dumps(payload)


RECORDING = Recording(Path("/v/clip.mp4"), 300.0, 60.0, 1920, 1080, 10, 0.0)


class TestFrameOffsets:
    def test_single_frame_is_the_labelled_instant(self) -> None:
        assert frame_offsets(1, spread_s=2.0) == (0.0,)

    def test_odd_counts_stay_centred(self) -> None:
        assert frame_offsets(3, spread_s=2.0) == (-2.0, 0.0, 2.0)
        assert frame_offsets(5, spread_s=1.0) == (-2.0, -1.0, 0.0, 1.0, 2.0)

    def test_even_counts_straddle_the_instant(self) -> None:
        assert frame_offsets(2, spread_s=2.0) == (-1.0, 1.0)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_at_least_one_frame(self, bad: int) -> None:
        with pytest.raises(ValueError):
            frame_offsets(bad, spread_s=1.0)


class TestLabels:
    def test_round_trip(self, tmp_path: Path) -> None:
        cases = [
            SceneCase(Path("/v/a.mp4"), 12.5, "pre_round", notes="buy phase, knife out"),
            SceneCase(Path("/v/a.mp4"), 90.0, "early", expected_agent="Jett"),
        ]
        path = tmp_path / "labels.json"
        write_cases(path, cases)
        assert load_cases(path) == cases

    def test_unlabelled_cases_are_skipped_with_a_count(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        path.write_text(
            json.dumps(
                {
                    "cases": [
                        {"clip": "/v/a.mp4", "t": 1.0, "expected_phase": ""},
                        {"clip": "/v/a.mp4", "t": 2.0, "expected_phase": "early"},
                    ]
                }
            )
        )
        cases = load_cases(path)
        assert [c.timestamp_s for c in cases] == [2.0]

    def test_all_unlabelled_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        path.write_text(
            json.dumps({"cases": [{"clip": "/v/a.mp4", "t": 1.0, "expected_phase": ""}]})
        )
        with pytest.raises(LabelsError, match="no labelled"):
            load_cases(path)

    def test_unknown_phase_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        path.write_text(
            json.dumps({"cases": [{"clip": "/v/a.mp4", "t": 1.0, "expected_phase": "shopping"}]})
        )
        with pytest.raises(LabelsError, match="shopping"):
            load_cases(path)

    def test_missing_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(LabelsError, match="cannot read"):
            load_cases(tmp_path / "nope.json")

    def test_scaffold_writes_placeholders_at_a_fixed_interval(self, tmp_path: Path) -> None:
        frames_dir = tmp_path / "frames"
        cases = scaffold_cases(
            RECORDING, every_s=120.0, ffmpeg=FakeFfmpeg(), width=640, frames_dir=frames_dir
        )
        assert [c.timestamp_s for c in cases] == [0.0, 120.0, 240.0]
        assert all(c.expected_phase == "" for c in cases)
        assert all("frames" in c.notes for c in cases)
        assert len(list(frames_dir.glob("*.jpg"))) == 3

    def test_scaffold_then_load_tells_you_to_label(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        write_cases(path, scaffold_cases(RECORDING, 150.0, FakeFfmpeg(), 640, tmp_path / "f"))
        with pytest.raises(LabelsError, match="no labelled"):
            load_cases(path)


class TestRunCases:
    def cases(self) -> list[SceneCase]:
        return [
            SceneCase(Path("/v/a.mp4"), 10.0, "early"),
            SceneCase(Path("/v/a.mp4"), 20.0, "pre_round"),
        ]

    def run(self, transport: FakeTransport, tmp_path: Path, frames: int = 1) -> SceneReport:
        return run_cases(
            self.cases(),
            transport=transport,
            probe=lambda path: RECORDING,
            ffmpeg=FakeFfmpeg(),
            model="qwen3-vl:8b",
            frames_per_case=frames,
            spread_s=1.0,
            width=640,
            frames_dir=tmp_path / "frames",
            timeout_s=30.0,
        )

    def test_records_detected_versus_expected(self, tmp_path: Path) -> None:
        report = self.run(FakeTransport(situation_json("early"), situation_json("early")), tmp_path)
        assert report.model == "qwen3-vl:8b"
        assert report.frames_per_case == 1
        assert [o.detected_phase for o in report.outcomes] == ["early", "early"]
        assert [o.phase_ok for o in report.outcomes] == [True, False]
        assert report.accuracy() == pytest.approx(0.5)
        assert isinstance(report.outcomes[0].situation, Situation)
        assert report.outcomes[0].frame is not None

    def test_one_call_per_case_with_the_requested_frame_count(self, tmp_path: Path) -> None:
        transport = FakeTransport(situation_json(), situation_json())
        report = self.run(transport, tmp_path, frames=3)
        assert len(transport.calls) == 2
        assert all(len(c.images_b64) == 3 for c in transport.calls)
        assert report.frames_per_case == 3

    def test_unparseable_reply_is_recorded_not_raised(self, tmp_path: Path) -> None:
        report = self.run(FakeTransport("I see a game", situation_json("pre_round")), tmp_path)
        assert report.outcomes[0].detected_phase == "unreadable"
        assert report.outcomes[0].error is not None
        assert report.outcomes[1].phase_ok is True
        assert report.accuracy() == pytest.approx(0.5)

    def test_transport_failure_is_recorded_not_raised(self, tmp_path: Path) -> None:
        report = self.run(
            FakeTransport(OllamaError("refused"), situation_json("pre_round")), tmp_path
        )
        assert report.outcomes[0].detected_phase == "unreadable"
        assert "OllamaError" in (report.outcomes[0].error or "")

    def test_also_scores_agent_and_map_when_labelled(self, tmp_path: Path) -> None:
        cases = [
            SceneCase(Path("/v/a.mp4"), 10.0, "early", expected_agent="Jett", expected_map="Bind")
        ]
        report = run_cases(
            cases,
            transport=FakeTransport(situation_json("early")),
            probe=lambda path: RECORDING,
            ffmpeg=FakeFfmpeg(),
            model="m",
            frames_per_case=1,
            spread_s=1.0,
            width=640,
            frames_dir=tmp_path / "frames",
            timeout_s=30.0,
        )
        outcome = report.outcomes[0]
        assert outcome.agent_ok is True  # Jett detected
        assert outcome.map_ok is False  # said Ascent, labelled Bind
        assert outcome.field_results()["map"] == ("Bind", "Ascent", False)

    def test_progress_callback(self, tmp_path: Path) -> None:
        seen: list[tuple[int, int]] = []
        run_cases(
            self.cases(),
            transport=FakeTransport(situation_json(), situation_json()),
            probe=lambda path: RECORDING,
            ffmpeg=FakeFfmpeg(),
            model="m",
            frames_per_case=1,
            spread_s=1.0,
            width=640,
            frames_dir=tmp_path / "frames",
            timeout_s=30.0,
            on_progress=lambda done, total: seen.append((done, total)),
        )
        assert seen == [(1, 2), (2, 2)]


class TestReporting:
    def report(self, tmp_path: Path, *phases: tuple[str, str]) -> SceneReport:
        cases = [
            SceneCase(Path("/v/a.mp4"), float(i), expected)
            for i, (expected, _) in enumerate(phases)
        ]
        transport = FakeTransport(*[situation_json(detected) for _, detected in phases])
        return run_cases(
            cases,
            transport=transport,
            probe=lambda path: RECORDING,
            ffmpeg=FakeFfmpeg(),
            model="m",
            frames_per_case=1,
            spread_s=1.0,
            width=640,
            frames_dir=tmp_path / "frames",
            timeout_s=30.0,
        )

    def test_confusion_counts_expected_against_detected(self, tmp_path: Path) -> None:
        report = self.report(
            tmp_path, ("early", "pre_round"), ("early", "pre_round"), ("early", "early")
        )
        assert report.confusion()[("early", "pre_round")] == 2
        assert report.confusion()[("early", "early")] == 1
        assert report.by_phase()["early"] == (1, 3)

    def test_scene_accuracy_of_an_empty_report_is_zero(self) -> None:
        assert scene_accuracy(()) == 0.0

    def test_render_names_the_model_accuracy_and_worst_confusion(self, tmp_path: Path) -> None:
        report = self.report(tmp_path, ("early", "pre_round"), ("pre_round", "pre_round"))
        text = render_scene_report(report)
        assert "m" in text
        assert "50%" in text
        assert "early" in text and "pre_round" in text
        assert "1 frame" in text
        assert "expected early" in text  # the failing case is listed

    def test_render_flags_a_model_that_always_says_the_same_phase(self, tmp_path: Path) -> None:
        report = self.report(
            tmp_path, ("early", "pre_round"), ("mid", "pre_round"), ("retake", "pre_round")
        )
        text = render_scene_report(report)
        assert "always" in text.lower()

    def test_to_dict_is_json_serialisable(self, tmp_path: Path) -> None:
        report = self.report(tmp_path, ("early", "early"))
        json.dumps(report.to_dict())
        assert report.to_dict()["accuracy"] == pytest.approx(1.0)
        assert report.to_dict()["cases"][0]["expected_phase"] == "early"
