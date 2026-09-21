"""End-to-end review of one recording. Wires the layers together and owns ledger semantics."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.context import PlayerContext, merge_context
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.parse import Finding
from round_review.coaching.review import WindowResult, review_window
from round_review.config import Config
from round_review.errors import (
    AlreadyProcessed,
    CapExceeded,
    OllamaError,
    ParseError,
    RoundReviewError,
    VideoError,
)
from round_review.ledger import (
    LedgerEntry,
    Status,
    append_entry,
    calls_today,
    is_processed,
    read_ledger,
    recording_key,
)
from round_review.llm.transport import Transport, UrllibTransport
from round_review.report.json_report import write_report_json
from round_review.report.markdown import Report, write_report
from round_review.video.frames import extract_frames, extract_single_frame
from round_review.video.probe import CommandRunner, Recording, SubprocessRunner, probe
from round_review.video.windows import select_windows

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class Deps:
    config: Config
    probe_runner: CommandRunner
    ffmpeg_runner: CommandRunner
    transport: Transport
    clock: Clock


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_default_deps(config: Config) -> Deps:
    return Deps(
        config=config,
        probe_runner=SubprocessRunner(config.ffprobe_path),
        ffmpeg_runner=SubprocessRunner(config.ffmpeg_path),
        transport=UrllibTransport(config.ollama_url, num_ctx=config.num_ctx),
        clock=_utc_now,
    )


def key_for(path: Path) -> str:
    """Ledger/report identity of a recording as it exists on disk right now."""
    try:
        st = path.stat()
    except OSError as exc:
        raise VideoError(f"{path}: cannot stat: {exc}") from exc
    return recording_key(path, st.st_size, st.st_mtime)


def report_dir_for(config: Config, path: Path) -> Path:
    return config.reports_dir / f"{path.stem}_{key_for(path)}"


def _record(
    deps: Deps,
    key: str,
    path: Path,
    status: Status,
    model_calls: int,
    report_path: Path | None,
    error: RoundReviewError | None,
) -> None:
    append_entry(
        deps.config.ledger_path,
        LedgerEntry(
            key=key,
            path=str(path),
            processed_at=deps.clock(),
            model_calls=model_calls,
            report_path=str(report_path) if report_path else None,
            status=status,
            error=f"{type(error).__name__}: {error}" if error else None,
        ),
    )


def _attach_exact_evidence(
    result: WindowResult, recording: Recording, deps: Deps, frames_dir: Path
) -> tuple[WindowResult, list[str]]:
    """Re-cut each finding's evidence frame at its exact timestamp. The sampled frame can be
    up to half a sampling interval early, which reads as 'the picture is from just before'."""
    warnings: list[str] = []
    findings: list[Finding] = []
    for n, finding in enumerate(result.findings, start=1):
        out = frames_dir / f"e{result.window.index:02d}_{n:02d}.jpg"
        try:
            exact = extract_single_frame(
                recording, finding.timestamp_s, deps.ffmpeg_runner, deps.config.frame_width, out
            )
        except VideoError as exc:
            warnings.append(
                f"evidence frame at t={finding.timestamp_s:.1f}s could not be cut ({exc}); "
                "using the nearest sampled frame"
            )
            findings.append(finding)
            continue
        findings.append(replace(finding, evidence_frame=exact))
    return replace(result, findings=tuple(findings)), warnings


def review_file(
    path: Path,
    deps: Deps,
    context: PlayerContext | None = None,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> Report:
    """Review one recording and write its report. The ledger entry is always the last write,
    and every failure class is recorded before being re-raised. `on_progress(done, total)`
    is called once windows are known and after each window."""
    cfg = deps.config
    context = merge_context(
        context or PlayerContext(), PlayerContext(notes=cfg.player_notes or None)
    )
    key = key_for(path)
    entries = read_ledger(cfg.ledger_path)
    if not force and is_processed(entries, key):
        raise AlreadyProcessed(f"{path.name} is already in the ledger (key {key})")
    history_calls = calls_today(entries, deps.clock().date())

    out_dir = cfg.reports_dir / f"{path.stem}_{key}"
    frames_dir = out_dir / "frames"
    calls = 0
    results: list[WindowResult] = []
    unparseable_windows = 0
    warnings: list[str] = []
    knowledge = load_knowledge()

    try:
        recording = probe(path, deps.probe_runner)
        windows = select_windows(
            recording.duration_s, cfg.window_s, cfg.windows_per_file, cfg.edge_skip_s
        )
        log.info("%s: %.1fs, reviewing %d window(s)", path.name, recording.duration_s, len(windows))
        if on_progress:
            on_progress(0, len(windows))
        for window in windows:
            samples = extract_frames(
                recording, window, deps.ffmpeg_runner, cfg.fps, cfg.frame_width, frames_dir
            )
            try:
                result = review_window(
                    window,
                    samples,
                    deps.transport,
                    cfg.model,
                    history_calls + calls,
                    cfg.daily_call_cap,
                    cfg.request_timeout_s,
                    context,
                    knowledge,
                    cfg.situation_pass,
                )
            except ParseError as exc:
                unparseable_windows += 1
                calls += exc.model_calls
                warnings.append(str(exc))
                results.append(
                    WindowResult(window, tuple(samples), (), exc.model_calls, (str(exc),))
                )
                if on_progress:
                    on_progress(len(results), len(windows))
                continue
            calls += result.model_calls
            result, evidence_warnings = _attach_exact_evidence(result, recording, deps, frames_dir)
            warnings.extend(evidence_warnings)
            results.append(result)
            if on_progress:
                on_progress(len(results), len(windows))

        if windows and unparseable_windows == len(windows):
            raise ParseError(f"{path.name}: all {len(windows)} windows unparseable", model_calls=0)
    except CapExceeded as exc:
        # One failed attempt still costs nothing at the transport, but calls so far do count.
        _record(deps, key, path, "skipped", calls, None, exc)
        raise
    except OllamaError as exc:
        # The failed call was sent; count it so a flapping server cannot bypass the cap.
        _record(deps, key, path, "failed", calls + 1, None, exc)
        raise
    except (VideoError, ParseError) as exc:
        _record(deps, key, path, "failed", calls, None, exc)
        raise

    report = Report(recording, deps.clock(), cfg.model, tuple(results), tuple(warnings))
    report_path = write_report(report, out_dir)
    write_report_json(report, out_dir)
    _record(deps, key, path, "ok", calls, report_path, None)
    return report
