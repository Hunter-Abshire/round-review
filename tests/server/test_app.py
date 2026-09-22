import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from round_review.coaching.context import PlayerContext
from round_review.coaching.question import QuestionSpec
from round_review.config import Config
from round_review.errors import VideoError
from round_review.ledger import LedgerEntry, append_entry
from round_review.pipeline import key_for, report_dir_for
from round_review.server.app import ConfigHolder, create_app
from round_review.server.jobs import JobOptions, JobQueue, ProgressFn
from tests.video.test_probe import PROBE_JSON


class FakeProbe:
    """ffprobe stand-in: readable for everything except a file named like a broken one."""

    def run(self, args: list[str]) -> str:
        if "broken" in args[-1]:
            raise VideoError("moov atom not found")
        return PROBE_JSON


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def tmp_path_for_config(cfg: Config) -> Path:
    """Each test gets its own config file next to its ledger."""
    return cfg.ledger_path.parent / "config.toml"


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    (tmp_path / "vids" / "sub").mkdir(parents=True)
    return Config(
        reports_dir=tmp_path / "reports",
        ledger_path=tmp_path / "ledger.jsonl",
        recordings_dir=tmp_path / "vids",
    )


@pytest.fixture
def clip(cfg: Config) -> Path:
    assert cfg.recordings_dir is not None
    p = cfg.recordings_dir / "sub" / "match.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000)
    return p


class FakeRun:
    """Synchronous stand-in for the pipeline: writes a report.json and returns."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.calls: list[Path] = []
        self.contexts: list[PlayerContext] = []
        self.options: list[JobOptions] = []
        self.questions: list[QuestionSpec] = []
        self.question_contexts: list[PlayerContext] = []

    def __call__(
        self, path: Path, context: PlayerContext, options: JobOptions, on_progress: ProgressFn
    ) -> None:
        self.calls.append(path)
        self.contexts.append(context)
        self.options.append(options)
        out = report_dir_for(self.cfg, path)
        (out / "frames").mkdir(parents=True, exist_ok=True)
        (out / "frames" / "w00_002.jpg").write_bytes(b"\xff\xd8jpeg")
        (out / "frames" / "e00_01.jpg").write_bytes(b"\xff\xd8exact")
        (out / "report.json").write_text(
            json.dumps(
                {
                    "recording": {"name": path.name, "duration_s": 5.0},
                    "windows": [
                        {
                            "index": 0,
                            "findings": [
                                {"timestamp_s": 2.0, "evidence_frame": "frames/w00_002.jpg"}
                            ],
                        }
                    ],
                }
            )
        )
        on_progress(1, 1)


ANSWER = {
    "question": "How could I have used utility here?",
    "start_s": 40.0,
    "end_s": 52.0,
    "answerable": True,
    "answer": "You pushed that angle with your smoke still up.",
    "what_you_could_see": "The ability icon is lit at t=44.0s.",
    "what_you_could_not_know": "",
    "assumptions": [],
    "alternatives": [
        {"action": "Smoke the far angle first.", "why": "It halves the exposure."},
        {"action": "Wait for your team.", "why": "It keeps the trade."},
    ],
    "confidence": 0.8,
    "warnings": [],
}


@pytest.fixture
def client(cfg: Config) -> TestClient:
    run = FakeRun(cfg)

    def answer(path: Path, context: PlayerContext, spec: QuestionSpec) -> dict[str, object]:
        run.questions.append(spec)
        run.question_contexts.append(context)
        return ANSWER

    jobs = JobQueue(run, clock=lambda: NOW, run_question=answer)
    jobs.start()
    holder = ConfigHolder(cfg, tmp_path_for_config(cfg))
    app = create_app(holder, jobs, probe_runner=FakeProbe())
    app.state.fake_run = run
    with TestClient(app) as c:
        yield c  # type: ignore[misc]
    jobs.stop()


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": "qwen3-vl:8b"}


def test_clips_lists_recursively_with_new_status(client: TestClient, clip: Path) -> None:
    (clip.parent / "notes.txt").write_text("x")
    r = client.get("/api/clips")
    assert r.status_code == 200
    (item,) = r.json()["clips"]
    assert item["name"] == "match.mp4"
    assert item["path"] == str(clip)
    assert item["key"] == key_for(clip)
    assert item["status"] == "new"
    assert item["job_id"] is None
    assert item["size_bytes"] == clip.stat().st_size


def test_clips_status_from_ledger(client: TestClient, cfg: Config, clip: Path) -> None:
    append_entry(
        cfg.ledger_path,
        LedgerEntry(key_for(clip), str(clip), NOW, 3, "/r/report.md", "failed", "VideoError: x"),
    )
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["status"] == "failed"
    assert item["error"] == "VideoError: x"


def test_clips_without_recordings_dir(tmp_path: Path) -> None:
    cfg = Config(reports_dir=tmp_path, ledger_path=tmp_path / "l.jsonl")
    jobs = JobQueue(lambda p, c, o, f: None, clock=lambda: NOW)
    with TestClient(
        create_app(ConfigHolder(cfg, tmp_path_for_config(cfg)), jobs, probe_runner=FakeProbe())
    ) as c:
        r = c.get("/api/clips")
    assert r.status_code == 200
    assert r.json()["clips"] == []
    assert "recordings_dir" in r.json()["warning"]


def test_submit_job_then_poll_then_report(client: TestClient, clip: Path) -> None:
    r = client.post("/api/jobs", json={"path": str(clip)})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] in ("queued", "running", "done")
    assert job["key"] == key_for(clip)

    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    r = client.get(f"/api/jobs/{job['id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["windows_done"] == 1

    (item,) = client.get("/api/clips").json()["clips"]
    assert item["status"] == "done"

    r = client.get(f"/api/reports/{job['key']}")
    assert r.status_code == 200
    assert r.json()["recording"]["name"] == "match.mp4"

    assert client.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]


def test_submit_rejects_paths_outside_recordings_dir(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    r = client.post("/api/jobs", json={"path": str(outside)})
    assert r.status_code == 400
    assert (
        client.post("/api/jobs", json={"path": str(tmp_path / "vids" / "missing.mp4")}).status_code
        == 404
    )


def test_unknown_job_and_report_are_404(client: TestClient) -> None:
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/reports/0123456789abcdef").status_code == 404
    assert client.get("/api/reports/../etc").status_code in (404, 422)


def test_video_supports_range_requests(client: TestClient, clip: Path) -> None:
    key = key_for(clip)
    r = client.get(f"/api/media/{key}/video", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-9/")
    assert r.content == clip.read_bytes()[:10]
    assert r.headers["content-type"] == "video/mp4"
    full = client.get(f"/api/media/{key}/video")
    assert full.status_code == 200 and full.headers.get("accept-ranges") == "bytes"


def test_frames_served_with_name_validation(client: TestClient, clip: Path) -> None:
    key = key_for(clip)
    client.post("/api/jobs", json={"path": str(clip)})
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    r = client.get(f"/api/media/{key}/frames/w00_002.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/media/{key}/frames/report.json").status_code == 404
    assert client.get(f"/api/media/{key}/frames/..%2Freport.json").status_code == 404
    assert client.get("/api/media/ffffffffffffffff/frames/w00_002.jpg").status_code == 404


def test_submit_job_passes_context(client: TestClient, clip: Path) -> None:
    r = client.post(
        "/api/jobs",
        json={
            "path": str(clip),
            "context": {"rank": "Gold 2", "agent": "Jett", "map": "", "side": "Attack"},
        },
    )
    assert r.status_code == 202, r.text
    assert r.json()["context"] == {
        "rank": "Gold 2",
        "agent": "Jett",
        "map": None,
        "side": "attack",
        "focus": None,
        "notes": None,
    }
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    run = client.app.state.fake_run  # type: ignore[attr-defined]
    assert run.contexts == [PlayerContext(rank="Gold 2", agent="Jett", side="attack")]


def test_exact_evidence_frames_are_served(client: TestClient, clip: Path) -> None:
    key = key_for(clip)
    client.post("/api/jobs", json={"path": str(clip)})
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    assert client.get(f"/api/media/{key}/frames/e00_01.jpg").status_code == 200


def test_knowledge_endpoint_lists_agents_maps_ranks_and_checklist(client: TestClient) -> None:
    r = client.get("/api/knowledge")
    assert r.status_code == 200
    body = r.json()
    assert {"id": "jett", "name": "Jett", "role": "duelist"} in body["agents"]
    assert any(m["id"] == "ascent" and m["name"] == "Ascent" for m in body["maps"])
    assert "Gold" in body["ranks"]
    assert any(
        c["id"] == "crosshair.head_level" for cat in body["checklist"] for c in cat["checks"]
    )


def test_clips_include_duration_and_estimated_windows(client: TestClient, clip: Path) -> None:
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["duration_s"] == pytest.approx(123.456)
    assert item["estimated_windows"] == 5  # full coverage of 123s at 12s windows


def test_clips_survive_an_unreadable_file(client: TestClient, cfg: Config, clip: Path) -> None:
    assert cfg.recordings_dir is not None
    (cfg.recordings_dir / "broken.mp4").write_bytes(b"not a video")
    items = client.get("/api/clips").json()["clips"]
    names = {i["name"] for i in items}
    assert {"match.mp4", "broken.mp4"} <= names
    broken = next(i for i in items if i["name"] == "broken.mp4")
    assert broken["duration_s"] is None
    assert broken["estimated_windows"] is None


def test_submit_with_force_re_reviews_a_done_clip(client: TestClient, clip: Path) -> None:
    first = client.post("/api/jobs", json={"path": str(clip)})
    assert first.status_code == 202
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    again = client.post("/api/jobs", json={"path": str(clip), "force": True})
    assert again.status_code == 202
    assert again.json()["id"] != first.json()["id"]
    assert again.json()["options"]["force"] is True
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    run = client.app.state.fake_run  # type: ignore[attr-defined]
    assert len(run.calls) == 2
    assert run.options[1].force is True


def test_submit_passes_coverage_overrides(client: TestClient, clip: Path) -> None:
    r = client.post(
        "/api/jobs",
        json={"path": str(clip), "coverage": "sampled", "max_span_s": 60, "max_windows": 8},
    )
    assert r.status_code == 202, r.text
    assert r.json()["options"] == {
        "force": False,
        "coverage": "sampled",
        "max_span_s": 60.0,
        "max_windows": 8,
    }
    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    options = client.app.state.fake_run.options[0]  # type: ignore[attr-defined]
    assert (options.coverage, options.max_span_s, options.max_windows) == ("sampled", 60.0, 8)


def test_submit_rejects_an_unknown_coverage_mode(client: TestClient, clip: Path) -> None:
    r = client.post("/api/jobs", json={"path": str(clip), "coverage": "everything"})
    assert r.status_code == 422


def test_settings_describes_the_review_defaults(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["coverage"] == "full"
    assert body["window_s"] == 12.0
    assert body["model"] == "qwen3-vl:8b"
    assert body["situation_pass"] is True
    assert body["coverage_modes"] == ["full", "rounds", "sampled"]


def test_partial_ledger_status_is_reported_as_partial(
    client: TestClient, cfg: Config, clip: Path
) -> None:
    append_entry(
        cfg.ledger_path,
        LedgerEntry(key_for(clip), str(clip), NOW, 4, "/r/report.md", "partial", None),
    )
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["status"] == "partial"


def test_settings_reports_whether_hud_checking_is_usable(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["hud_check"] is True
    assert body["hud_ready"] is False  # nothing learned yet
    assert ":" in body["hud_missing_characters"]


def test_settings_reports_a_trained_hud_as_ready(cfg: Config, tmp_path: Path) -> None:
    from round_review.vision.digits import CLOCK_CHARACTERS, DigitTemplates
    from round_review.vision.raster import Glyph

    store = tmp_path / "digits.json"
    DigitTemplates({c: (Glyph(("#",), 0.5),) for c in CLOCK_CHARACTERS}).save(store)
    trained = dataclasses.replace(cfg, hud_templates_path=store)
    jobs = JobQueue(lambda p, c, o, f: None, clock=lambda: NOW)
    with TestClient(
        create_app(
            ConfigHolder(trained, tmp_path_for_config(trained)), jobs, probe_runner=FakeProbe()
        )
    ) as c:
        body = c.get("/api/settings").json()
    assert body["hud_ready"] is True
    assert body["hud_missing_characters"] == []


def test_clips_estimate_the_review_time_from_past_reviews(
    client: TestClient, cfg: Config, clip: Path
) -> None:
    # a past review of 20 windows that took 10 minutes: 30s a window
    append_entry(
        cfg.ledger_path,
        LedgerEntry(
            "other", "/v/old.mp4", NOW, 40, "/r/x.md", "ok", None, windows=20, duration_s=600.0
        ),
    )
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["estimated_windows"] == 5
    assert item["estimated_seconds"] == pytest.approx(150.0)
    assert item["estimated_time"] == "about 2 minutes"  # 150s


def test_clips_have_no_estimate_before_anything_has_been_timed(
    client: TestClient, clip: Path
) -> None:
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["estimated_seconds"] is None
    assert item["estimated_time"] is None


def test_a_long_review_is_estimated_in_hours(client: TestClient, cfg: Config, clip: Path) -> None:
    append_entry(
        cfg.ledger_path,
        LedgerEntry("o", "/v/o.mp4", NOW, 2, None, "ok", None, windows=1, duration_s=1800.0),
    )
    (item,) = client.get("/api/clips").json()["clips"]
    assert item["estimated_time"] == "about 2 hours 30 minutes"  # 5 windows at 30 min each


def test_ask_queues_a_question_and_returns_the_answer(client: TestClient, clip: Path) -> None:
    r = client.post(
        "/api/ask",
        json={
            "path": str(clip),
            "start_s": 40,
            "end_s": 52,
            "question": "How could I have used utility here?",
            "context": {"rank": "Gold 2"},
        },
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "question"
    assert job["question"]["question"] == "How could I have used utility here?"
    assert job["question"]["start_s"] == 40.0

    assert client.app.state.jobs.wait_idle(timeout=5)  # type: ignore[attr-defined]
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done"
    assert done["answer"]["answer"].startswith("You pushed")
    assert len(done["answer"]["alternatives"]) == 2
    asked = client.app.state.fake_run.questions  # type: ignore[attr-defined]
    assert asked[0].question == "How could I have used utility here?"
    assert client.app.state.fake_run.question_contexts[0].rank == "Gold 2"  # type: ignore[attr-defined]


def test_ask_rejects_an_empty_question(client: TestClient, clip: Path) -> None:
    r = client.post(
        "/api/ask", json={"path": str(clip), "start_s": 10, "end_s": 20, "question": "   "}
    )
    assert r.status_code == 422


def test_ask_rejects_a_path_outside_the_recordings_folder(
    client: TestClient, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    r = client.post(
        "/api/ask", json={"path": str(outside), "start_s": 1, "end_s": 2, "question": "why?"}
    )
    assert r.status_code == 400


def test_ask_accepts_a_bare_click_with_no_end(client: TestClient, clip: Path) -> None:
    r = client.post("/api/ask", json={"path": str(clip), "start_s": 60, "question": "why?"})
    assert r.status_code == 202, r.text
    assert r.json()["question"]["end_s"] == 60.0  # the pipeline widens it


def test_settings_publishes_the_question_limits(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["max_question_span_s"] == 60.0
    assert body["question_frames"] == 6


def test_config_lists_every_setting_with_its_value_and_meaning(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert body["path"].endswith("config.toml")
    names = {f["name"] for f in body["fields"]}
    assert {"model", "coverage", "max_span_s", "notes_dir", "situation_pass"} <= names
    coverage = next(f for f in body["fields"] if f["name"] == "coverage")
    assert coverage["value"] == "full"
    assert coverage["kind"] == "choice"
    assert coverage["choices"] == ["rounds", "full", "sampled"]
    assert coverage["help"].endswith(".")
    assert coverage["group"] == "How much to review"
    assert body["groups"][0] == "Recordings"


def test_config_offers_the_models_ollama_has(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from round_review.llm.models import InstalledModel

    monkeypatch.setattr(
        "round_review.server.app.list_models",
        lambda url: [InstalledModel("qwen3-vl:8b", 6_000_000_000, "z")],
    )
    model = next(f for f in client.get("/api/config").json()["fields"] if f["name"] == "model")
    assert model["choices"] == ["qwen3-vl:8b"]


def test_config_flags_settings_an_environment_variable_has_taken_over(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUND_REVIEW_MODEL", "forced:1b")
    model = next(f for f in client.get("/api/config").json()["fields"] if f["name"] == "model")
    assert model["overridden_by_env"] == "ROUND_REVIEW_MODEL"


def test_saving_config_writes_the_file_and_takes_effect(client: TestClient, cfg: Config) -> None:
    r = client.put("/api/config", json={"values": {"coverage": "sampled", "max_span_s": 60}})
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True

    from round_review.config import load_config

    written = load_config(client.app.state.config_path, env={}, data_dir=cfg.reports_dir.parent)  # type: ignore[attr-defined]
    assert written.coverage == "sampled"
    # and the running app uses it immediately, without a restart
    assert client.get("/api/settings").json()["coverage"] == "sampled"


def test_saving_an_invalid_value_changes_nothing(client: TestClient) -> None:
    r = client.put("/api/config", json={"values": {"coverage": "sideways"}})
    assert r.status_code == 422
    assert "coverage" in r.json()["detail"]
    assert client.get("/api/settings").json()["coverage"] == "full"


def test_saving_an_unknown_setting_is_refused(client: TestClient) -> None:
    assert client.put("/api/config", json={"values": {"bogus": 1}}).status_code == 422


def test_clearing_a_setting_restores_its_default(client: TestClient) -> None:
    client.put("/api/config", json={"values": {"max_span_s": 60}})
    assert client.get("/api/settings").json()["max_span_s"] == 60.0
    client.put("/api/config", json={"values": {"max_span_s": None}})
    assert client.get("/api/settings").json()["max_span_s"] == 0.0


def test_clips_carry_the_date_and_what_was_identified(
    client: TestClient, cfg: Config, tmp_path: Path
) -> None:
    from round_review.config import identities_file
    from round_review.identity import ClipIdentity, write_identity

    assert cfg.recordings_dir is not None
    named = cfg.recordings_dir / "Valorant_09-21-2026_22-04-48-733.mp4"
    named.write_bytes(b"\x00" * 2000)
    write_identity(identities_file(cfg), ClipIdentity(key_for(named), "Jett", "Ascent", "attack"))

    item = next(c for c in client.get("/api/clips").json()["clips"] if c["name"] == named.name)
    assert item["agent"] == "Jett"
    assert item["map"] == "Ascent"
    assert item["side"] == "attack"
    assert item["played_at"].startswith("2026-09-21T22:04")


def test_a_clip_with_nothing_known_says_so_rather_than_guessing(
    client: TestClient, clip: Path
) -> None:
    item = next(c for c in client.get("/api/clips").json()["clips"] if c["name"] == clip.name)
    assert item["agent"] is None
    assert item["map"] is None
    assert item["played_at"] is not None  # the file's own time always works


def test_rating_a_finding_is_recorded_and_counted(client: TestClient) -> None:
    body = {
        "key": "0123456789abcdef",
        "check_id": "positioning.one_line",
        "timestamp_s": 12.0,
        "verdict": "wrong",
    }
    assert client.post("/api/feedback", json=body).status_code == 201
    assert client.post("/api/feedback", json={**body, "verdict": "useful"}).status_code == 201
    stats = client.get("/api/feedback").json()
    # Same clip, same check, same moment: the later opinion replaces the earlier one.
    assert stats["rated"] == 1
    assert stats["hit_rate"] == 1.0


def test_an_unknown_verdict_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/feedback",
        json={
            "key": "0123456789abcdef",
            "check_id": "a.b",
            "timestamp_s": 1.0,
            "verdict": "maybe",
        },
    )
    assert response.status_code == 422


def test_a_bad_key_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/feedback",
        json={"key": "../../etc", "check_id": "a.b", "timestamp_s": 1.0, "verdict": "useful"},
    )
    assert response.status_code in {400, 404, 422}


def test_no_feedback_yet_has_no_hit_rate(client: TestClient) -> None:
    stats = client.get("/api/feedback").json()
    assert stats["rated"] == 0 and stats["hit_rate"] is None
