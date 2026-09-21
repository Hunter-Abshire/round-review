import json
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from round_review.coaching.context import PlayerContext
from round_review.config import Config
from round_review.errors import (
    AlreadyProcessed,
    CapExceeded,
    OllamaError,
    ParseError,
    VideoError,
)
from round_review.ledger import read_ledger
from round_review.llm.transport import ChatRequest, ChatResponse, UrllibTransport
from round_review.pipeline import Deps, make_default_deps, review_file
from tests.llm.test_transport import FakeResponse
from tests.video.test_probe import PROBE_JSON

FIXED_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def good(ts: float = 65.0) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "timestamp_s": ts,
                    "check_id": "crosshair.head_level",
                    "category": "timing",
                    "observation": "o",
                    "visible_evidence": "v",
                    "information_available_to_player": "i",
                    "information_revealed_later": "",
                    "assumption_flags": [],
                    "suggested_alternative": "s",
                    "confidence": 0.7,
                }
            ]
        }
    )


class FakeProbe:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def run(self, args: list[str]) -> str:
        if self.fail:
            raise VideoError("ffprobe exit 1: moov atom not found")
        return PROBE_JSON  # duration 123.456 -> 3 windows


class FakeFfmpeg:
    def __init__(self, frames: int = 2, fail: bool = False) -> None:
        self.frames = frames
        self.fail = fail
        self.calls = 0

    def run(self, args: list[str]) -> str:
        self.calls += 1
        if self.fail:
            raise VideoError("ffmpeg exit 1")
        pattern = Path(args[-1])
        pattern.parent.mkdir(parents=True, exist_ok=True)
        if "%" not in pattern.name:
            pattern.write_bytes(b"exact")
            return ""
        for i in range(1, self.frames + 1):
            (pattern.parent / (pattern.name % i)).write_bytes(b"jpg")
        return ""


class FakeTransport:
    def __init__(self, *contents: str | Exception) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        item = self.responses.popleft()
        if isinstance(item, Exception):
            raise item
        return ChatResponse(item, 0, 0)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "vids" / "match.mp4"
    p.parent.mkdir()
    p.write_bytes(b"\x00" * 100)
    return p


def make_deps(tmp_path: Path, transport: FakeTransport, **overrides: object) -> Deps:
    probe_runner = overrides.pop("probe", FakeProbe())
    ffmpeg_runner = overrides.pop("ffmpeg", FakeFfmpeg())
    if "cap" in overrides:
        overrides["daily_call_cap"] = int(overrides.pop("cap"))  # type: ignore[call-overload]
    settings: dict[str, object] = {
        "reports_dir": tmp_path / "reports",
        "ledger_path": tmp_path / "ledger.jsonl",
        "window_s": 12.0,
        "coverage": "sampled",
        "windows_per_file": 3,
        "edge_skip_s": 30.0,
        "daily_call_cap": 30,
        "situation_pass": False,
    }
    settings.update(overrides)
    return Deps(
        config=Config(**settings),  # type: ignore[arg-type]
        probe_runner=probe_runner,  # type: ignore[arg-type]
        ffmpeg_runner=ffmpeg_runner,  # type: ignore[arg-type]
        transport=transport,
        clock=lambda: FIXED_NOW,
    )


def test_happy_path_writes_report_then_ledger(video: Path, tmp_path: Path) -> None:
    # PROBE_JSON duration 123.456 -> usable 63.456 -> 3 windows of 12s
    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport)
    report = review_file(video, deps, context=PlayerContext(rank="Gold 2"))

    assert len(report.results) == 3
    assert sum(len(r.findings) for r in report.results) >= 1
    assert "Rank: Gold 2." in transport.calls[0].prompt
    # evidence frames are re-cut at the exact finding timestamp, not the nearest sample
    for result in report.results:
        for finding in result.findings:
            assert finding.evidence_frame is not None
            assert finding.evidence_frame.name.startswith("e")
            assert finding.evidence_frame.read_bytes() == b"exact"

    entries = read_ledger(deps.config.ledger_path)
    assert len(entries) == 1
    assert entries[0].status == "ok"
    assert entries[0].model_calls == 3
    assert entries[0].processed_at == FIXED_NOW
    assert entries[0].report_path is not None
    report_path = Path(entries[0].report_path)
    assert report_path.exists()
    assert report_path.parent.parent == deps.config.reports_dir
    assert (report_path.parent / "frames").is_dir()


def test_already_processed_raises_and_makes_no_calls(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport)
    review_file(video, deps)
    with pytest.raises(AlreadyProcessed):
        review_file(video, deps)
    assert len(transport.calls) == 3
    assert len(read_ledger(deps.config.ledger_path)) == 1


def test_configured_context_sent_on_every_window_and_retry(video: Path, tmp_path: Path) -> None:
    deps = make_deps(tmp_path, FakeTransport())
    config = replace(deps.config, num_ctx=32768)
    transport = make_default_deps(config).transport
    assert isinstance(transport, UrllibTransport)
    bodies = []
    responses = deque(["invalid JSON", '{"findings": []}', '{"findings": []}', '{"findings": []}'])

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse({"message": {"content": responses.popleft()}})

    report = review_file(
        video, replace(deps, config=config, transport=replace(transport, opener=opener))
    )
    assert len(report.results) == 3
    assert len(bodies) == 4
    assert all(body["options"]["num_ctx"] == 32768 for body in bodies)
    assert read_ledger(config.ledger_path)[0].model_calls == 4


def test_force_reprocesses(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(*([good(65.0)] * 6))
    deps = make_deps(tmp_path, transport)
    review_file(video, deps)
    review_file(video, deps, force=True)
    assert len(read_ledger(deps.config.ledger_path)) == 2


def test_probe_failure_recorded_as_failed(video: Path, tmp_path: Path) -> None:
    deps = make_deps(tmp_path, FakeTransport(), probe=FakeProbe(fail=True))
    with pytest.raises(VideoError, match="moov"):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "failed"
    assert entry.error is not None and entry.error.startswith("VideoError")
    assert entry.model_calls == 0


def test_ffmpeg_failure_recorded_as_failed(video: Path, tmp_path: Path) -> None:
    deps = make_deps(tmp_path, FakeTransport(), ffmpeg=FakeFfmpeg(fail=True))
    with pytest.raises(VideoError):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "failed"


def test_cap_counts_ledger_history(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport, cap=3)
    review_file(video, deps)  # uses 3 calls today
    other = video.with_name("other.mp4")
    other.write_bytes(b"\x01" * 50)
    with pytest.raises(CapExceeded):
        review_file(other, deps)
    assert len(transport.calls) == 3


def test_one_unparseable_window_becomes_warning(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), "garbage", "more garbage", good(85.0))
    deps = make_deps(tmp_path, transport)
    report = review_file(video, deps)
    assert len(report.results) == 3
    assert report.results[1].findings == ()
    assert any("unparseable" in w for w in report.warnings)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "ok"
    assert entry.model_calls == 4


def test_all_windows_unparseable_is_failed(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(*(["x"] * 6))
    deps = make_deps(tmp_path, transport)
    with pytest.raises(ParseError, match="all 3 windows"):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "failed"
    assert entry.model_calls == 6


def test_all_windows_filtered_is_a_successful_empty_review(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(0.0), good(0.0), good(0.0))
    deps = make_deps(tmp_path, transport)
    report = review_file(video, deps)
    assert all(not r.findings and r.warnings for r in report.results)
    assert read_ledger(deps.config.ledger_path)[0].status == "ok"


@pytest.mark.parametrize("notes", [None, "Focus on this specific round."])
def test_default_player_notes_apply_to_automatic_reviews_but_user_notes_win(
    video: Path, tmp_path: Path, notes: str | None
) -> None:
    transport = FakeTransport(*(['{"findings": []}'] * 3))
    deps = make_deps(tmp_path, transport)
    deps = replace(
        deps, config=replace(deps.config, player_notes="New player, explain every skill.")
    )
    review_file(video, deps, context=PlayerContext(notes=notes))
    assert f"Notes: {notes or deps.config.player_notes}." in transport.calls[0].prompt


@pytest.mark.integration
def test_end_to_end_real_ffmpeg_fake_model(sample_video: Path, tmp_path: Path) -> None:
    from round_review.video.probe import SubprocessRunner

    # 5 s clip -> a single whole-file window; the fake model returns one finding at 2.5 s
    transport = FakeTransport(good(2.5))
    deps = make_deps(
        tmp_path,
        transport,
        probe=SubprocessRunner("ffprobe"),
        ffmpeg=SubprocessRunner("ffmpeg"),
    )
    report = review_file(sample_video, deps, context=PlayerContext(rank="Gold 2", agent="Jett"))
    assert len(report.results) == 1
    (finding,) = report.results[0].findings
    assert finding.evidence_frame is not None and finding.evidence_frame.exists()
    assert finding.evidence_frame.name == "e00_01.jpg"
    assert len(transport.calls[0].images_b64) == 5  # 1 fps over 5 s
    (entry,) = read_ledger(deps.config.ledger_path)
    text = Path(entry.report_path or "").read_text(encoding="utf-8")
    assert "# Review: sample.mp4" in text
    assert "![t=2.5s](frames/e00_01.jpg)" in text


def test_progress_callback_and_json_report(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport)
    seen: list[tuple[int, int]] = []
    review_file(video, deps, on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]
    (entry,) = read_ledger(deps.config.ledger_path)
    report_dir = Path(entry.report_path or "").parent
    assert (report_dir / "report.json").exists()


def test_key_for_and_report_dir_for(video: Path, tmp_path: Path) -> None:
    from round_review.pipeline import key_for, report_dir_for

    deps = make_deps(tmp_path, FakeTransport())
    key = key_for(video)
    assert len(key) == 16
    assert report_dir_for(deps.config, video) == deps.config.reports_dir / f"match_{key}"


def test_situation_pass_doubles_calls_and_records_situation(video: Path, tmp_path: Path) -> None:
    from tests.coaching.test_review import SITUATION

    transport = FakeTransport(SITUATION, good(35.0), SITUATION, good(65.0), SITUATION, good(85.0))
    deps = make_deps(tmp_path, transport)
    deps = Deps(
        config=Config(
            reports_dir=deps.config.reports_dir,
            ledger_path=deps.config.ledger_path,
            coverage="sampled",
            situation_pass=True,
        ),
        probe_runner=deps.probe_runner,
        ffmpeg_runner=deps.ffmpeg_runner,
        transport=transport,
        clock=deps.clock,
    )
    report = review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.model_calls == 6
    assert all(r.situation is not None for r in report.results)
    assert report.results[0].context.agent == "Jett"


def test_exact_frame_failure_keeps_sample_frame_with_warning(video: Path, tmp_path: Path) -> None:
    class FlakyFfmpeg(FakeFfmpeg):
        def run(self, args: list[str]) -> str:
            if "%" not in Path(args[-1]).name:
                raise VideoError("ffmpeg exit 1: seek failed")
            return super().run(args)

    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport, ffmpeg=FlakyFfmpeg())
    report = review_file(video, deps)
    findings = [f for r in report.results for f in r.findings]
    assert findings and all(
        f.evidence_frame is not None and f.evidence_frame.name.startswith("w") for f in findings
    )
    assert any("evidence" in w.lower() for w in report.warnings)


def test_full_coverage_tiles_the_recording(video: Path, tmp_path: Path) -> None:
    # PROBE_JSON duration 123.456, edge skip 30 -> usable 30..93.456 -> five 12s tiles
    transport = FakeTransport(*[good(35.0)] * 6)
    deps = make_deps(tmp_path, transport, coverage="full")
    report = review_file(video, deps)
    assert len(report.results) == 5
    assert all(r.window.source == "tiled" for r in report.results)
    covered = sum(r.window.end_s - r.window.start_s for r in report.results)
    assert covered / 123.456 > 0.45


def test_first_minute_only(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(*[good(35.0)] * 6)
    deps = make_deps(tmp_path, transport, coverage="full", max_span_s=24.0)
    report = review_file(video, deps)
    assert len(report.results) == 2
    assert report.results[-1].window.end_s == pytest.approx(54.0)


def test_cap_mid_review_saves_a_partial_report(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), good(45.0))
    deps = make_deps(tmp_path, transport, coverage="full", cap=2)
    report = review_file(video, deps)
    assert len(report.results) == 2  # stopped early, kept the work
    assert any("cap" in w.lower() for w in report.warnings)
    assert any("partial" in w.lower() for w in report.warnings)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "partial"
    assert entry.model_calls == 2
    assert len(transport.calls) == 2
    assert entry.report_path is not None and Path(entry.report_path).exists()


def test_ollama_failure_mid_review_saves_a_partial_report(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), OllamaError("HTTP 500"))
    deps = make_deps(tmp_path, transport, coverage="full")
    report = review_file(video, deps)
    assert len(report.results) == 1
    assert any("OllamaError" in w for w in report.warnings)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "partial"
    assert entry.model_calls == 2  # the failed call is counted too


def test_failure_on_the_first_window_still_fails_the_file(video: Path, tmp_path: Path) -> None:
    deps = make_deps(tmp_path, FakeTransport(OllamaError("refused")))
    with pytest.raises(OllamaError):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "failed"


def test_all_windows_abstaining_is_a_successful_empty_review(video: Path, tmp_path: Path) -> None:
    from tests.coaching.test_review import SITUATION

    buy = json.loads(SITUATION)
    buy["phase"] = "pre_round"
    transport = FakeTransport(*[json.dumps(buy)] * 6)
    deps = make_deps(tmp_path, transport, situation_pass=True)
    report = review_file(video, deps)
    assert sum(len(r.findings) for r in report.results) == 0
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "ok"
    assert all(any("coaching skipped" in w for w in result.warnings) for result in report.results)


def test_progress_reports_the_tiled_window_count(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(*[good(35.0)] * 6)
    deps = make_deps(tmp_path, transport, coverage="full")
    seen: list[tuple[int, int]] = []
    review_file(video, deps, on_progress=lambda done, total: seen.append((done, total)))
    assert seen[0] == (0, 5)
    assert seen[-1] == (5, 5)
