import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pytest

from round_review.config import Config
from round_review.errors import (
    AlreadyProcessed,
    CapExceeded,
    OllamaError,
    ParseError,
    VideoError,
)
from round_review.ledger import read_ledger
from round_review.llm.transport import ChatRequest, ChatResponse
from round_review.pipeline import Deps, review_file
from tests.video.test_probe import PROBE_JSON

FIXED_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def good(ts: float = 65.0) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "timestamp_s": ts,
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
    cfg = Config(
        reports_dir=tmp_path / "reports",
        ledger_path=tmp_path / "ledger.jsonl",
        window_s=12.0,
        windows_per_file=3,
        edge_skip_s=30.0,
        daily_call_cap=int(overrides.get("cap", 30)),  # type: ignore[call-overload]
    )
    return Deps(
        config=cfg,
        probe_runner=overrides.get("probe", FakeProbe()),  # type: ignore[arg-type]
        ffmpeg_runner=overrides.get("ffmpeg", FakeFfmpeg()),  # type: ignore[arg-type]
        transport=transport,
        clock=lambda: FIXED_NOW,
    )


def test_happy_path_writes_report_then_ledger(video: Path, tmp_path: Path) -> None:
    # PROBE_JSON duration 123.456 -> usable 63.456 -> 3 windows of 12s
    transport = FakeTransport(good(35.0), good(65.0), good(85.0))
    deps = make_deps(tmp_path, transport)
    report = review_file(video, deps, context="Gold 2")

    assert len(report.results) == 3
    assert sum(len(r.findings) for r in report.results) >= 1
    assert transport.calls[0].prompt.startswith("Player context: Gold 2")

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


def test_ollama_failure_recorded_as_failed_with_calls_so_far(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), OllamaError("HTTP 500"))
    deps = make_deps(tmp_path, transport)
    with pytest.raises(OllamaError):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "failed"
    assert entry.model_calls == 2  # the failed call is counted too
    assert entry.error is not None and "OllamaError" in entry.error


def test_cap_exceeded_recorded_as_skipped(video: Path, tmp_path: Path) -> None:
    transport = FakeTransport(good(35.0), good(65.0))
    deps = make_deps(tmp_path, transport, cap=2)
    with pytest.raises(CapExceeded):
        review_file(video, deps)
    (entry,) = read_ledger(deps.config.ledger_path)
    assert entry.status == "skipped"
    assert entry.model_calls == 2
    assert len(transport.calls) == 2


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
    report = review_file(sample_video, deps, context="Gold 2, Jett")
    assert len(report.results) == 1
    (finding,) = report.results[0].findings
    assert finding.evidence_frame is not None and finding.evidence_frame.exists()
    assert len(transport.calls[0].images_b64) == 5  # 1 fps over 5 s
    (entry,) = read_ledger(deps.config.ledger_path)
    text = Path(entry.report_path or "").read_text(encoding="utf-8")
    assert "# Review: sample.mp4" in text
    assert "![t=2.5s](frames/w00_003.jpg)" in text
