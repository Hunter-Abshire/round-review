"""End-to-end review of one recording. Wires the layers together and owns ledger semantics."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from round_review.coaching.context import PlayerContext, merge_context
from round_review.coaching.frames import select_situation_frames
from round_review.coaching.history import MatchHabits, append_match, read_history, tag_habits
from round_review.coaching.knowledge import CoachingKnowledge, load_knowledge
from round_review.coaching.parse import Finding
from round_review.coaching.prompt import (
    RETRY_NUDGE,
    SITUATION_SCHEMA,
    SITUATION_SYSTEM_PROMPT,
    build_situation_prompt,
)
from round_review.coaching.question import (
    ANSWER_SCHEMA,
    Answer,
    QuestionSpec,
    build_question_prompt,
    build_question_system_prompt,
    clamp_span,
    parse_answer,
)
from round_review.coaching.review import WindowResult, review_window
from round_review.coaching.session import build_session_summary, habit_counts
from round_review.coaching.situation import Situation, parse_situation
from round_review.config import Config, default_data_dir, habits_file, identities_file
from round_review.diagnosis import abstention_warning
from round_review.errors import (
    AlreadyProcessed,
    CapExceeded,
    OllamaError,
    ParseError,
    ReviewAbandoned,
    RoundReviewError,
    VideoError,
)
from round_review.identity import identity_index, write_identity
from round_review.ledger import (
    LedgerEntry,
    Status,
    append_entry,
    calls_today,
    is_processed,
    read_ledger,
    recording_key,
)
from round_review.llm.client import build_chat_request, send_review
from round_review.llm.transport import Transport, UrllibTransport
from round_review.reference.corpus import build_corpus, load_notes
from round_review.reference.search import Hit, build_index, search
from round_review.report.json_report import write_report_json
from round_review.report.markdown import Report, write_report
from round_review.video.frames import encode_frame_b64, extract_frames, extract_single_frame
from round_review.video.probe import CommandRunner, Recording, SubprocessRunner, probe
from round_review.video.windows import Window, plan_windows
from round_review.vision.agent_icons import AgentTemplates, identify_from_frames
from round_review.vision.digits import DigitTemplates
from round_review.vision.hud import HudRead, optional_region, parse_region, parse_regions, read_hud
from round_review.vision.state import HudState, find_deaths, read_state
from round_review.vision.timeline import (
    RoundSpan,
    round_at,
    scan_clock,
    scan_numbers,
    segment_rounds,
)

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
        transport=UrllibTransport(
            config.ollama_url, num_ctx=config.num_ctx, keep_alive=config.ollama_keep_alive
        ),
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
    windows: int = 0,
    duration_s: float = 0.0,
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
            windows=windows,
            duration_s=duration_s,
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


def _read_window_hud(
    recording: Recording,
    window: Window,
    deps: Deps,
    templates: DigitTemplates,
    frames_dir: Path,
) -> HudRead | None:
    """Read the round clock once per window, from the middle frame. Costs one ffmpeg crop
    and no model call; returns None when HUD checking is off or unconfigured."""
    cfg = deps.config
    if not (cfg.hud_check and cfg.situation_pass) or not templates.characters():
        return None
    try:
        region = parse_region(cfg.hud_timer_region)
    except RoundReviewError as exc:
        log.warning("hud_timer_region is unusable, skipping HUD checks: %s", exc)
        return None
    midpoint = (window.start_s + window.end_s) / 2
    return read_hud(
        recording,
        midpoint,
        region,
        deps.ffmpeg_runner,
        templates,
        out_dir=frames_dir / "hud",
        min_confidence=cfg.hud_min_confidence,
        threshold=cfg.hud_threshold,
    )


def _identify_agent(
    recording: Recording, deps: Deps, windows: Sequence[Window], out_dir: Path
) -> str | None:
    """Name the agent from the ability icons, when they have been learned.

    This is the same argument as the clock: which agent is on screen is a fact the HUD
    renders, so it should not be left to a model reading a portrait at thumbnail size.
    """
    cfg = deps.config
    if not cfg.hud_check:
        return None
    templates = AgentTemplates.load(
        cfg.hud_agent_templates_path or default_data_dir() / "hud-agent-icons.json"
    )
    if not templates.agents_to_kits:
        return None
    try:
        abilities = parse_regions(cfg.hud_ability_regions)
    except RoundReviewError as exc:
        log.warning("hud_ability_regions is unusable, not identifying the agent: %s", exc)
        return None
    if not abilities:
        return None
    # A handful of moments spread across the clip, so one obscured frame cannot decide it.
    moments = [(w.start_s + w.end_s) / 2 for w in windows[:: max(1, len(windows) // 5)]][:5]
    name, score = identify_from_frames(
        recording,
        moments,
        abilities,
        deps.ffmpeg_runner,
        templates,
        out_dir=out_dir / "agent-icons",
        min_confidence=cfg.hud_agent_min_confidence,
        threshold=cfg.hud_threshold,
    )
    if name:
        log.info("ability icons identify the agent as %s (%.0f%%)", name, score * 100)
    return name


def _scan_deaths(
    recording: Recording, deps: Deps, templates: DigitTemplates, out_dir: Path
) -> tuple[float, ...]:
    """Find the player's deaths by scanning the health number. Off until the health region
    is measured, because an unconfigured crop would invent deaths at random."""
    cfg = deps.config
    if not cfg.hud_check or not templates.characters():
        return ()
    try:
        region = optional_region(cfg.hud_health_region)
    except RoundReviewError as exc:
        log.warning("hud_health_region is unusable, not scanning for deaths: %s", exc)
        return ()
    if region is None:
        return ()
    samples = scan_numbers(
        recording,
        region,
        deps.ffmpeg_runner,
        templates,
        out_dir=out_dir / "scan",
        min_confidence=cfg.hud_min_confidence,
        interval_s=cfg.scan_interval_s,
        threshold=cfg.hud_threshold,
        stem="health",
    )
    deaths = find_deaths(samples)
    log.info("health scan: %d sample(s), %d death(s)", len(samples), len(deaths))
    return deaths


def _read_window_state(
    recording: Recording,
    window: Window,
    deps: Deps,
    templates: DigitTemplates,
    frames_dir: Path,
) -> HudState | None:
    """Read health, credits and lit abilities at the middle of a window. None when nothing
    is configured, so an uncalibrated HUD costs no ffmpeg calls at all."""
    cfg = deps.config
    if not cfg.hud_check or not templates.characters():
        return None
    try:
        regions = {
            name: region
            for name, raw in (
                ("health", cfg.hud_health_region),
                ("credits", cfg.hud_credits_region),
            )
            if (region := optional_region(raw)) is not None
        }
        abilities = parse_regions(cfg.hud_ability_regions)
    except RoundReviewError as exc:
        log.warning("HUD state regions are unusable, skipping them: %s", exc)
        return None
    if not regions and not abilities:
        return None
    return read_state(
        recording,
        (window.start_s + window.end_s) / 2,
        regions,
        abilities,
        deps.ffmpeg_runner,
        templates,
        out_dir=frames_dir / "hud",
        min_confidence=cfg.hud_min_confidence,
        threshold=cfg.hud_threshold,
        lit_threshold=cfg.hud_lit_threshold,
        lit_min_fraction=cfg.hud_lit_min_fraction,
    )


def _round_of(spans: tuple[RoundSpan, ...], window: Window) -> int | None:
    span = round_at(spans, window.start_s)
    return span.index if span else None


def _scan_rounds(
    recording: Recording, deps: Deps, templates: DigitTemplates, out_dir: Path
) -> tuple[RoundSpan, ...]:
    """Find the round boundaries by scanning the clock. One ffmpeg pass, no model calls.

    Returns nothing when the clock reader is off or untrained, which is a normal state: the
    caller falls back to time-based windows rather than reviewing an empty plan.
    """
    cfg = deps.config
    if not cfg.hud_check or not templates.characters():
        return ()
    try:
        region = parse_region(cfg.hud_timer_region)
    except RoundReviewError as exc:
        log.warning("hud_timer_region is unusable, not scanning for rounds: %s", exc)
        return ()
    samples = scan_clock(
        recording,
        region,
        deps.ffmpeg_runner,
        templates,
        out_dir=out_dir / "scan",
        min_confidence=cfg.hud_min_confidence,
        interval_s=cfg.scan_interval_s,
        threshold=cfg.hud_threshold,
    )
    spans = segment_rounds(samples, min_confidence=cfg.hud_min_confidence)
    log.info("clock scan: %d sample(s), %d round(s)", len(samples), len(spans))
    return spans


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
    started = time.monotonic()
    hud_templates = (
        DigitTemplates.load(cfg.hud_templates_path)
        if cfg.hud_check and cfg.situation_pass and cfg.hud_templates_path
        else DigitTemplates({})
    )

    try:
        recording = probe(path, deps.probe_runner)
        spans = _scan_rounds(recording, deps, hud_templates, out_dir)
        deaths = _scan_deaths(recording, deps, hud_templates, out_dir)
        windows = plan_windows(
            recording.duration_s,
            window_s=cfg.window_s,
            coverage=cfg.coverage,  # type: ignore[arg-type]
            windows_per_file=cfg.windows_per_file,
            edge_skip_s=cfg.edge_skip_s,
            max_windows=cfg.max_windows,
            max_span_s=cfg.max_span_s,
            spans=spans,
            deaths=deaths,
        )
        # Even a tiled review benefits from round labels, so map any window the scan covers.
        if spans:
            windows = [
                w if w.round_index is not None else replace(w, round_index=_round_of(spans, w))
                for w in windows
            ]
    except VideoError as exc:
        _record(deps, key, path, "failed", 0, None, exc, 0, time.monotonic() - started)
        raise

    # Before any model call: the icons name the agent, and a told agent still wins.
    if context.agent is None:
        detected = _identify_agent(recording, deps, windows, out_dir)
        if detected:
            context = replace(context, agent=detected)
            warnings.append(f"agent read from the ability icons as {detected}")

    log.info("%s: %.1fs, reviewing %d window(s)", path.name, recording.duration_s, len(windows))
    if on_progress:
        on_progress(0, len(windows))

    # A full review is dozens of windows, so a failure part-way through must not throw away
    # the windows already reviewed: stop, keep the results, and record the review as partial.
    stopped: RoundReviewError | None = None
    abstain_streak = 0
    for window in windows:
        try:
            samples = extract_frames(
                recording, window, deps.ffmpeg_runner, cfg.fps, cfg.frame_width, frames_dir
            )
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
                hud=_read_window_hud(recording, window, deps, hud_templates, frames_dir),
                buy_phase_max_s=cfg.buy_phase_max_s,
                hud_min_confidence=cfg.hud_min_confidence,
                situation_frames=cfg.situation_frames,
                coach_frames=cfg.coach_frames,
                state=_read_window_state(recording, window, deps, hud_templates, frames_dir),
            )
        except ParseError as exc:
            # One unreadable window is a warning; every window unreadable fails the file.
            unparseable_windows += 1
            calls += exc.model_calls
            warnings.append(str(exc))
            results.append(
                WindowResult(window, (), (), (), exc.model_calls, (str(exc),), parse_failed=True)
            )
            if on_progress:
                on_progress(len(results), len(windows))
            continue
        except CapExceeded as exc:
            stopped = exc
            break
        except OllamaError as exc:
            calls += 1  # the failed call was sent
            stopped = exc
            break
        except VideoError as exc:
            stopped = exc
            break
        calls += result.model_calls
        result, evidence_warnings = _attach_exact_evidence(result, recording, deps, frames_dir)
        warnings.extend(evidence_warnings)
        results.append(result)
        if on_progress:
            on_progress(len(results), len(windows))

        # A model that cannot read the scene skips every window. Spending hours to find that
        # out helps nobody, so stop and say what to check.
        abstain_streak = abstain_streak + 1 if result.abstained else 0
        if cfg.abstain_streak_limit and abstain_streak >= cfg.abstain_streak_limit:
            stopped = ReviewAbandoned(
                f"{abstain_streak} windows in a row were skipped before coaching, so the rest "
                "of this recording is unlikely to be any different. Check what the model is "
                "reading with `round-review scenes validate`, and set abstain_streak_limit = 0 "
                "to review the whole thing anyway"
            )
            break

    diagnosis = abstention_warning(results)
    if diagnosis:
        warnings.append(diagnosis)

    if stopped is not None:
        if not results:
            status: Status = "skipped" if isinstance(stopped, CapExceeded) else "failed"
            _record(deps, key, path, status, calls, None, stopped, 0, time.monotonic() - started)
            raise stopped
        warnings.append(
            f"review stopped after {len(results)} of {len(windows)} windows "
            f"({type(stopped).__name__}: {stopped}); this report is partial"
        )
    elif windows and unparseable_windows == len(windows):
        unreadable = ParseError(f"{path.name}: all {len(windows)} windows unparseable")
        _record(deps, key, path, "failed", calls, None, unreadable, 0, time.monotonic() - started)
        raise unreadable

    # Tagged before this match is recorded, so it is never compared against itself.
    counts = habit_counts(results)
    history_path = habits_file(cfg)
    trends = tag_habits(counts, read_history(history_path), windows=len(results))

    report = Report(
        recording,
        deps.clock(),
        cfg.model,
        tuple(results),
        tuple(warnings),
        stopped_reason=f"{type(stopped).__name__}: {stopped}" if stopped else None,
        summary=build_session_summary(
            results,
            rank=context.rank,
            checklist=knowledge.checklist,
            trends=trends,
        ),
    )
    report_path = write_report(report, out_dir)
    write_report_json(report, out_dir)
    # Record what the clip was, so the library can show it without another model call.
    identity = identity_index(key, results)
    if identity:
        write_identity(identities_file(cfg), identity)
    if counts:
        append_match(
            history_path,
            MatchHabits(key=key, reviewed_at=deps.clock(), windows=len(results), counts=counts),
        )
    _record(
        deps,
        key,
        path,
        "partial" if stopped else "ok",
        calls,
        report_path,
        None,
        len(results),
        time.monotonic() - started,
    )
    return report


def _references(
    cfg: Config, knowledge: CoachingKnowledge, question: str, context: PlayerContext
) -> list[Hit]:
    """Retrieve reference passages for a question. Never fails the question: an unreadable
    notes folder means no references, not no answer."""
    if cfg.reference_passages <= 0:
        return []
    try:
        notes = load_notes(cfg.notes_dir)
    except RoundReviewError as exc:
        log.warning("notes could not be read, answering without them: %s", exc)
        notes = []
    corpus = build_corpus(knowledge, notes)
    tags = [tag for tag in (context.agent, context.map) if tag]
    return search(
        build_index(corpus),
        question,
        limit=cfg.reference_passages,
        tags=tags,
        max_chars=cfg.max_reference_chars,
    )


def answer_question(
    path: Path,
    deps: Deps,
    spec: QuestionSpec,
    context: PlayerContext | None = None,
) -> Answer:
    """Answer one question about one stretch of a recording.

    Same two passes as a review of a window: read the scene, then reason about it. The
    difference is that the player chose the moment and the question, so nothing is skipped
    for being the buy phase; if they asked about it, they want an answer about it.
    """
    if not spec.question.strip():
        raise ValueError("question must not be empty")

    cfg = deps.config
    context = merge_context(
        context or PlayerContext(), PlayerContext(notes=cfg.player_notes or None)
    )
    knowledge = load_knowledge()
    recording = probe(path, deps.probe_runner)
    start, end = clamp_span(spec.start_s, spec.end_s, recording.duration_s, cfg.max_question_span_s)
    spec = QuestionSpec(start, end, spec.question)
    window = Window(0, start, end, "asked")

    frames_dir = report_dir_for(cfg, path) / "asked"
    samples = extract_frames(
        recording, window, deps.ffmpeg_runner, cfg.fps, cfg.frame_width, frames_dir
    )
    asked_samples = select_situation_frames(samples, cfg.question_frames)

    history_calls = calls_today(read_ledger(cfg.ledger_path), deps.clock().date())
    calls = 0
    situation: Situation | None = None

    if cfg.situation_pass:
        scene_samples = select_situation_frames(samples, cfg.situation_frames)
        request = build_chat_request(
            cfg.model,
            SITUATION_SYSTEM_PROMPT,
            build_situation_prompt(window, scene_samples, context),
            [encode_frame_b64(s.path) for s in scene_samples],
            SITUATION_SCHEMA,
            cfg.request_timeout_s,
        )
        response = send_review(request, deps.transport, history_calls + calls, cfg.daily_call_cap)
        calls += 1
        try:
            situation = parse_situation(response.content)
        except ParseError as exc:
            log.info("question situation pass unparseable, answering without it: %s", exc)
        else:
            context = merge_context(context, situation.to_context())

    system = build_question_system_prompt(knowledge, situation.phase if situation else None)
    references = _references(cfg, knowledge, spec.question, context)
    prompt = build_question_prompt(
        window, asked_samples, spec, context, situation, knowledge, references
    )
    images = [encode_frame_b64(s.path) for s in asked_samples]

    last_error: ParseError | None = None
    for attempt in range(2):
        text = prompt if attempt == 0 else prompt + RETRY_NUDGE
        request = build_chat_request(
            cfg.model, system, text, images, ANSWER_SCHEMA, cfg.request_timeout_s
        )
        response = send_review(request, deps.transport, history_calls + calls, cfg.daily_call_cap)
        calls += 1
        try:
            answer = parse_answer(response.content, spec)
        except ParseError as exc:
            last_error = exc
            log.warning("answer attempt %d unparseable: %s", attempt + 1, exc)
        else:
            return replace(answer, sources=tuple(hit.passage.title for hit in references))

    assert last_error is not None
    raise ParseError(f"could not read an answer after a retry: {last_error}", model_calls=calls)
