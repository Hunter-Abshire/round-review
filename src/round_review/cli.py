"""click entry point. Thin: parses arguments, wires default deps, maps errors to exit codes."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

import click
import uvicorn

from round_review.coaching.context import PlayerContext, context_from_mapping
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.question import QuestionSpec
from round_review.config import Config, default_config_path, default_data_dir, load_config
from round_review.errors import HudError, RoundReviewError
from round_review.ledger import is_processed, read_ledger, recording_key
from round_review.pipeline import Deps, _utc_now, answer_question, make_default_deps, review_file
from round_review.reference.corpus import build_corpus, load_notes
from round_review.reference.search import build_index, search
from round_review.server.app import ConfigHolder, create_app
from round_review.server.jobs import JobOptions, JobQueue, ProgressFn
from round_review.validation.scenes import (
    SceneCase,
    SceneReport,
    load_cases,
    render_scene_report,
    run_cases,
    scaffold_cases,
    write_cases,
)
from round_review.video.probe import Recording, SubprocessRunner, probe
from round_review.vision.agent_icons import AgentTemplates, read_kit
from round_review.vision.calibrate import auto_threshold, best_threshold, score_thresholds
from round_review.vision.digits import DigitTemplates, load_templates
from round_review.vision.hud import (
    ADAPTIVE_THRESHOLD,
    AUTO_THRESHOLD,
    HudRead,
    Region,
    build_crop_args,
    learn_from_crop,
    optional_region,
    parse_region,
    parse_regions,
    read_hud,
)
from round_review.vision.raster import Gray, parse_pgm
from round_review.vision.state import find_deaths, read_state
from round_review.vision.timeline import scan_clock, scan_numbers, segment_rounds
from round_review.watcher import stat_snapshot, watch_loop

log = logging.getLogger("round_review")

# Module-level alias so tests can replace the blocking server start.
uvicorn_run = uvicorn.run


def _fail(exc: RoundReviewError) -> None:
    click.echo(f"{type(exc).__name__}: {exc}", err=True)
    sys.exit(1)


def _known_in_ledger(config: Config) -> Callable[[Path], bool]:
    def known(path: Path) -> bool:
        st = path.stat()
        return is_processed(
            read_ledger(config.ledger_path), recording_key(path, st.st_size, st.st_mtime)
        )

    return known


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to config.toml (default: the user data dir).",
)
@click.option("-v", "--verbose", is_flag=True, help="Debug logging.")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, verbose: bool) -> None:
    """Local VOD coaching from recorded gameplay."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        ctx.obj = load_config(config_path, os.environ)
    except RoundReviewError as exc:
        _fail(exc)


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--rank", default=None, help='Your rank, e.g. "Gold 2". Sets coaching priorities.')
@click.option(
    "--agent", default=None, help="Agent you played (auto-detected from the HUD if omitted)."
)
@click.option("--map", "game_map", default=None, help="Map (auto-detected if omitted).")
@click.option("--side", type=click.Choice(["attack", "defense"]), default=None)
@click.option(
    "--focus", default=None, help='What you want reviewed, e.g. "entries" or "post-plant".'
)
@click.option("--context", "notes", default=None, help="Free-text notes for the coach.")
@click.option(
    "--coverage",
    type=click.Choice(["full", "sampled"]),
    default=None,
    help="full reviews the whole recording (default); sampled takes a few windows.",
)
@click.option(
    "--first",
    "first_s",
    type=float,
    default=None,
    help="Review only the first N seconds of gameplay.",
)
@click.option(
    "--max-windows",
    type=int,
    default=None,
    help="Cap the number of reviewed windows; they stay spread across the recording.",
)
@click.option("--force", is_flag=True, help="Review even if the file is already in the ledger.")
@click.pass_obj
def review(
    config: Config,
    file: Path,
    rank: str | None,
    agent: str | None,
    game_map: str | None,
    side: str | None,
    focus: str | None,
    notes: str | None,
    coverage: str | None,
    first_s: float | None,
    max_windows: int | None,
    force: bool,
) -> None:
    """Review one recording and write its report."""
    config = _with_coverage(config, coverage, first_s, max_windows)
    deps: Deps = make_default_deps(config)
    context: PlayerContext = context_from_mapping(
        {
            "rank": rank,
            "agent": agent,
            "map": game_map,
            "side": side,
            "focus": focus,
            "notes": notes,
        }
    )
    try:
        report = review_file(file, deps, context=context, force=force)
    except RoundReviewError as exc:
        _fail(exc)
        return
    findings = sum(len(r.findings) for r in report.results)
    click.echo(
        f"Reviewed {file.name}: {findings} finding(s) across {len(report.results)} window(s). "
        f"Reports in {config.reports_dir}"
    )
    for warning in report.warnings:
        click.echo(f"warning: {warning}", err=True)


@main.command()
@click.argument(
    "directory",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.pass_obj
def watch(config: Config, directory: Path | None) -> None:
    """Watch a recordings folder and review each finished recording."""
    target = directory or config.recordings_dir
    if target is None:
        click.echo("ConfigError: recordings_dir is not configured and no DIRECTORY given", err=True)
        sys.exit(1)
    deps: Deps = make_default_deps(config)
    try:
        watch_loop(
            directory=target,
            review_fn=lambda p: review_file(p, deps),
            probe_fn=lambda p: probe(p, deps.probe_runner),
            is_known=_known_in_ledger(config),
            stat_fn=stat_snapshot,
            clock=deps.clock,
            now_fn=time.time,
            sleep_fn=time.sleep,
            poll_s=config.poll_s,
            quiet_polls=config.quiet_polls,
            min_age_s=config.min_age_s,
        )
    except KeyboardInterrupt:
        click.echo("stopped")
    except RoundReviewError as exc:
        _fail(exc)


def _with_coverage(
    config: Config, coverage: str | None, first_s: float | None, max_windows: int | None
) -> Config:
    if coverage is not None:
        config = replace(config, coverage=coverage)
    if first_s is not None:
        config = replace(config, max_span_s=first_s)
    if max_windows is not None:
        config = replace(config, max_windows=max_windows)
    return config


@main.group()
def reference() -> None:
    """Inspect what the coach can look up when you ask it a question.

    Questions are answered from the bundled briefs plus your own notes folder, retrieved by
    keyword. Nothing is fetched from the internet.
    """


@reference.command("search")
@click.argument("question", nargs=-1, required=True)
@click.option("--limit", type=int, default=5, help="How many passages to show.")
@click.option("--agent", default=None, help="Favour passages about this agent.")
@click.option("--map", "game_map", default=None, help="Favour passages about this map.")
@click.pass_obj
def reference_search(
    config: Config, question: tuple[str, ...], limit: int, agent: str | None, game_map: str | None
) -> None:
    """Show which reference passages a question would retrieve, and their scores."""
    text = " ".join(question)
    try:
        notes = load_notes(config.notes_dir)
    except RoundReviewError as exc:
        _fail(exc)
        return
    corpus = build_corpus(load_knowledge(), notes)
    tags = [tag for tag in (agent, game_map) if tag]
    hits = search(
        build_index(corpus),
        text,
        limit=limit,
        tags=tags,
        max_chars=config.max_reference_chars,
    )
    where = f" ({len(notes)} from your notes)" if notes else " (no notes folder configured)"
    click.echo(f"{len(corpus)} passages searched{where}\n")
    if not hits:
        click.echo("Nothing matched that question.")
        return
    for hit in hits:
        click.echo(f"[{hit.passage.kind}] {hit.passage.title}  (score {hit.score:.1f})")
        body = hit.passage.text.replace("\n", " ")
        click.echo(f"    {body[:160]}{'...' if len(body) > 160 else ''}\n")


@main.group()
def hud() -> None:
    """Read the round clock straight off the HUD, with no model involved.

    A vision model can call live play "buy phase"; the round timer cannot. Teach the digits
    once from your own footage and every review gains a veto over that misread.

    Workflow: `hud crop` to check the region lines up with your recordings, `hud learn` a
    few times to teach the digits, then `hud read` to confirm.
    """


def _hud_region(config: Config, override: str | None) -> Region:
    try:
        return parse_region(override or config.hud_timer_region)
    except RoundReviewError as exc:
        _fail(exc)
        raise  # unreachable, _fail exits


def _templates_path(config: Config, override: Path | None) -> Path:
    return override or config.hud_templates_path or default_data_dir() / "hud-digits.json"


def _hud_templates(config: Config, override: Path | None = None) -> DigitTemplates:
    """The glyphs that ship with the package, plus anything this player has taught."""
    return load_templates(_templates_path(config, override))


def _resolved_threshold(
    config: Config, recording: Recording, region: Region, templates: DigitTemplates
) -> int:
    """Measure a brightness cutoff for this recording when the config asks for one."""
    if config.hud_threshold != AUTO_THRESHOLD:
        return config.hud_threshold
    runner = SubprocessRunner(config.ffmpeg_path)
    out = default_data_dir() / "hud-auto"
    moments = [recording.duration_s * f for f in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    found = auto_threshold(
        lambda t: [
            read_hud(
                recording, m, region, runner, templates, out, config.hud_min_confidence, t
            ).clock_text
            for m in moments
        ]
    )
    return found if found is not None else ADAPTIVE_THRESHOLD


def agent_templates_path(config: Config, override: Path | None = None) -> Path:
    return (
        override or config.hud_agent_templates_path or default_data_dir() / "hud-agent-icons.json"
    )


@hud.command("crop")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamp_s", type=float, required=True, help="Timestamp to crop.")
@click.option("--region", default=None, help="Override the timer region as x,y,w,h fractions.")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("hud-timer.png"),
)
@click.pass_obj
def hud_crop(
    config: Config, file: Path, timestamp_s: float, region: str | None, out_path: Path
) -> None:
    """Save the timer region as a picture, so you can see whether it is pointed at the clock."""
    from round_review.vision.hud import build_crop_args

    box = _hud_region(config, region)
    runner = SubprocessRunner(config.ffmpeg_path)
    try:
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        runner.run(build_crop_args(file, timestamp_s, box, recording, out_path))
    except RoundReviewError as exc:
        _fail(exc)
        return
    click.echo(
        f"Wrote {out_path}. It should show only the round timer. If it does not, adjust "
        f"hud_timer_region in config.toml (currently {config.hud_timer_region}) and try again."
    )


@hud.command("learn")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamp_s", type=float, required=True, help="Timestamp to learn from.")
@click.option("--reads", required=True, help="What the timer says at that moment, e.g. 1:39.")
@click.option("--region", default=None, help="Override the timer region as x,y,w,h fractions.")
@click.option(
    "--store", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Template file."
)
@click.pass_obj
def hud_learn(
    config: Config,
    file: Path,
    timestamp_s: float,
    reads: str,
    region: str | None,
    store: Path | None,
) -> None:
    """Teach the digit shapes from one frame you have read with your own eyes."""
    box = _hud_region(config, region)
    path = _templates_path(config, store)
    try:
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
        # Learn at the cutoff the reader will actually use, or the glyphs will not match.
        threshold = _resolved_threshold(config, recording, box, _hud_templates(config, store))
        samples = learn_from_crop(
            recording,
            timestamp_s,
            box,
            SubprocessRunner(config.ffmpeg_path),
            path.parent / "hud-learn",
            reads,
            threshold=threshold,
        )
        templates = DigitTemplates.load(path).learn(samples)
        templates.save(path)
    except RoundReviewError as exc:
        _fail(exc)
        return
    known = "".join(sorted(templates.characters()))
    missing = templates.missing()
    click.echo(f"Learned {len(samples)} glyph(s) from t={timestamp_s:.1f}s into {path}.")
    click.echo(f"Known characters: {known or 'none'}")
    if missing:
        click.echo(
            f"Still missing: {' '.join(missing)}. Run `hud learn` at other timestamps until "
            "every digit and the colon are covered."
        )
    else:
        click.echo("Every digit and the colon are covered. Try `hud read` to confirm.")


@hud.command("read")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamps", type=float, multiple=True, required=True)
@click.option("--region", default=None)
@click.option("--store", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.pass_obj
def hud_read(
    config: Config,
    file: Path,
    timestamps: tuple[float, ...],
    region: str | None,
    store: Path | None,
) -> None:
    """Read the clock at given timestamps and say what it proves about the round."""
    box = _hud_region(config, region)
    path = _templates_path(config, store)
    try:
        templates = _hud_templates(config, store)
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
    except RoundReviewError as exc:
        _fail(exc)
        return
    threshold = _resolved_threshold(config, recording, box, templates)
    for timestamp in timestamps:
        read = read_hud(
            recording,
            timestamp,
            box,
            SubprocessRunner(config.ffmpeg_path),
            templates,
            out_dir=path.parent / "hud-read",
            min_confidence=config.hud_min_confidence,
            threshold=threshold,
        )
        click.echo(f"\nt={timestamp:.1f}s  crop: {read.crop_path}")
        if read.error:
            click.echo(f"  error: {read.error}")
            continue
        if read.clock_text is None:
            if read.glyph_count == 0:
                # Normal: the spike icon replaces the timer once the spike is down.
                click.echo(
                    "  nothing to read here. That is expected post-plant, when the spike "
                    "icon replaces the timer. If it happens in live play, check the region "
                    "with `hud crop --region`."
                )
            else:
                click.echo(
                    f"  found {read.glyph_count} glyph(s) but could not read them. Your HUD "
                    "may be scaled or ultrawide: check `hud crop --region`, then teach it "
                    f"with `hud learn --at {timestamp:.1f} --reads <what you see>`."
                )
            continue
        proof = (
            "live round, so any buy phase or post-plant call is wrong"
            if read.clock_s is not None and read.clock_s > config.buy_phase_max_s
            else "consistent with buy phase, mid round or post-plant alike"
        )
        click.echo(
            f"  clock {read.clock_text} ({read.clock_s:.0f}s), confidence {read.confidence:.0%}"
        )
        click.echo(f"  {proof}")


@hud.command("calibrate")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--reads",
    "readings",
    multiple=True,
    required=True,
    metavar="SECONDS=CLOCK",
    help="A timestamp and what the clock reads there, e.g. --reads 25=1:39. Give 6 or more.",
)
@click.option("--region", default=None)
@click.pass_obj
def hud_calibrate(
    config: Config, file: Path, readings: tuple[str, ...], region: str | None
) -> None:
    """Find the brightness cutoff that reads your HUD, by testing it against clocks you read.

    The adaptive cutoff moves with whatever scenery sits behind the translucent timer plate,
    so on real footage it reads the clock correctly in about a third of frames. Run this
    once, put the number in hud_threshold, and then teach the digits.
    """
    box = _hud_region(config, region)
    try:
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
    except RoundReviewError as exc:
        _fail(exc)
        return

    runner = SubprocessRunner(config.ffmpeg_path)
    out_dir = default_data_dir() / "hud-calibrate"
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[tuple[Gray, int]] = []
    labels: list[str] = []
    for reading in readings:
        stamp, _, text = reading.partition("=")
        if not text:
            _fail(HudError(f"--reads wants SECONDS=CLOCK, got {reading!r}"))
            return
        try:
            timestamp = float(stamp)
        except ValueError:
            _fail(HudError(f"--reads wants a number of seconds, got {stamp!r}"))
            return
        crop = out_dir / f"cal_{timestamp:.0f}.pgm"
        try:
            runner.run(build_crop_args(file, timestamp, box, recording, crop))
            samples.append((parse_pgm(crop.read_bytes()), len(text)))
        except (RoundReviewError, OSError) as exc:
            click.echo(f"  t={timestamp:.0f}s: could not crop ({exc})", err=True)
            continue
        labels.append(f"t={timestamp:.0f}s {text}")

    if len(samples) < 2:
        _fail(HudError("need at least two readable timestamps to calibrate"))
        return

    scores = score_thresholds(samples, labels=labels)
    click.echo(f"{'cutoff':>7}  {'reads':>7}")
    for score in scores:
        flag = "  <-- works" if score.perfect else ""
        click.echo(f"{score.threshold:>7}  {score.exact:>3}/{score.total}{flag}")

    pick = best_threshold(scores)
    if pick is None:
        click.echo(
            "\nNo cutoff read every sample. The region is probably wrong: check it with "
            "`hud crop --region` before calibrating.",
            err=True,
        )
        return
    click.echo(f"\nSet this in your config:\n\n    hud_threshold = {pick}\n")
    click.echo("Then teach the digits with `hud learn`.")


@hud.command("learn-agent")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamps", type=float, multiple=True, required=True)
@click.option("--agent", required=True, help="Which agent you were playing in these frames.")
@click.option("--store", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.pass_obj
def hud_learn_agent(
    config: Config, file: Path, timestamps: tuple[float, ...], agent: str, store: Path | None
) -> None:
    """Teach the ability icons for one agent, so the agent stops being a guess.

    Run it once per agent you play, on a frame where every ability is still unspent.
    """
    path = agent_templates_path(config, store)
    try:
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
        abilities = parse_regions(config.hud_ability_regions)
        templates = AgentTemplates.load(path)
    except RoundReviewError as exc:
        _fail(exc)
        return
    if not abilities:
        click.echo("hud_ability_regions is not set, so there are no icons to learn.", err=True)
        return
    threshold = _resolved_threshold(
        config, recording, _hud_region(config, None), _hud_templates(config, store)
    )
    learned = 0
    for timestamp in timestamps:
        kit = read_kit(
            recording,
            timestamp,
            abilities,
            SubprocessRunner(config.ffmpeg_path),
            out_dir=path.parent / "agent-icons",
            threshold=threshold,
        )
        if not kit:
            click.echo(f"  t={timestamp:.1f}s: could not crop every icon, skipped", err=True)
            continue
        templates = templates.learn(agent, kit)
        learned += 1
    if not learned:
        click.echo("Nothing learned.", err=True)
        return
    templates.save(path)
    click.echo(f"Learned {learned} icon set(s) for {agent}. Known agents: {templates.agents()}")
    click.echo(f"Stored in {path}")


@hud.command("state")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamps", type=float, multiple=True, required=True)
@click.option("--store", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.pass_obj
def hud_state(
    config: Config, file: Path, timestamps: tuple[float, ...], store: Path | None
) -> None:
    """Read health, credits and the ability icons, so the regions can be checked by eye."""
    path = _templates_path(config, store)
    try:
        templates = _hud_templates(config, store)
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
        regions = {
            name: region
            for name, raw in (
                ("health", config.hud_health_region),
                ("credits", config.hud_credits_region),
            )
            if (region := optional_region(raw)) is not None
        }
        abilities = parse_regions(config.hud_ability_regions)
    except RoundReviewError as exc:
        _fail(exc)
        return
    if not regions and not abilities:
        click.echo(
            "No health, credits or ability regions configured. Set hud_health_region, "
            "hud_credits_region or hud_ability_regions first; `hud crop --region` helps "
            "you find them.",
            err=True,
        )
        return
    threshold = _resolved_threshold(config, recording, _hud_region(config, None), templates)
    for timestamp in timestamps:
        state = read_state(
            recording,
            timestamp,
            regions,
            abilities,
            SubprocessRunner(config.ffmpeg_path),
            templates,
            out_dir=path.parent / "hud-state",
            min_confidence=config.hud_min_confidence,
            threshold=threshold,
            lit_threshold=config.hud_lit_threshold,
            lit_min_fraction=config.hud_lit_min_fraction,
        )
        click.echo(f"\nt={timestamp:.1f}s  {state.describe() or 'nothing readable'}")
        for error in state.errors:
            click.echo(f"  error: {error}")


@hud.command("deaths")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--store", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.pass_obj
def hud_deaths(config: Config, file: Path, store: Path | None) -> None:
    """Scan the health number and list the deaths it found.

    Check this against what actually happened before trusting death-anchored reviews: a
    health region that is a few pixels off will invent deaths rather than find none.
    """
    path = _templates_path(config, store)
    try:
        templates = _hud_templates(config, store)
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
        region = optional_region(config.hud_health_region)
    except RoundReviewError as exc:
        _fail(exc)
        return
    if region is None:
        click.echo("hud_health_region is not set, so deaths cannot be found.", err=True)
        return
    threshold = _resolved_threshold(config, recording, _hud_region(config, None), templates)
    samples = scan_numbers(
        recording,
        region,
        SubprocessRunner(config.ffmpeg_path),
        templates,
        out_dir=path.parent / "hud-deaths",
        min_confidence=config.hud_min_confidence,
        interval_s=config.scan_interval_s,
        threshold=threshold,
        stem="health",
    )
    readable = sum(1 for _t, value in samples if value is not None)
    deaths = find_deaths(samples)
    click.echo(f"{len(samples)} sample(s), {readable} readable, {len(deaths)} death(s)")
    for timestamp in deaths:
        click.echo(f"  {int(timestamp) // 60}:{int(timestamp) % 60:02d}")


@hud.command("rounds")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--region", default=None)
@click.option("--store", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.pass_obj
def hud_rounds(config: Config, file: Path, region: str | None, store: Path | None) -> None:
    """Scan the clock across the whole recording and list the rounds it found."""
    box = _hud_region(config, region)
    path = _templates_path(config, store)
    try:
        templates = _hud_templates(config, store)
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
    except RoundReviewError as exc:
        _fail(exc)
        return
    threshold = _resolved_threshold(config, recording, _hud_region(config, None), templates)
    samples = scan_clock(
        recording,
        box,
        SubprocessRunner(config.ffmpeg_path),
        templates,
        out_dir=path.parent / "hud-rounds",
        min_confidence=config.hud_min_confidence,
        interval_s=config.scan_interval_s,
        threshold=threshold,
    )
    readable = sum(1 for s in samples if s.clock_s is not None)
    spans = segment_rounds(samples, min_confidence=config.hud_min_confidence)
    click.echo(f"{len(samples)} sample(s), {readable} readable, {len(spans)} round(s)")
    for span in spans:
        start = f"{int(span.start_s) // 60}:{int(span.start_s) % 60:02d}"
        end = f"{int(span.end_s) // 60}:{int(span.end_s) % 60:02d}"
        click.echo(f"  round {span.index}: {start} to {end}")


@main.group()
def scenes() -> None:
    """Check whether the model can read the screen at all, before trusting its coaching.

    Workflow: `scenes scaffold` cuts frames and writes placeholders, you label them by
    looking at those frames, then `scenes validate` scores the situation pass against them.
    """


@scenes.command("scaffold")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--every", "every_s", type=float, default=30.0, help="Seconds between frames.")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("scene-labels.json"),
)
@click.option(
    "--frames-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to write the frames to look at (default: next to the labels file).",
)
@click.pass_obj
def scenes_scaffold(
    config: Config, file: Path, every_s: float, out_path: Path, frames_dir: Path | None
) -> None:
    """Cut a frame every N seconds and write unlabelled placeholders for each."""
    deps: Deps = make_default_deps(config)
    frames = frames_dir or out_path.parent / f"{out_path.stem}-frames"
    try:
        recording = probe(file, deps.probe_runner)
        cases = scaffold_cases(recording, every_s, deps.ffmpeg_runner, config.frame_width, frames)
        write_cases(out_path, cases)
    except RoundReviewError as exc:
        _fail(exc)
        return
    click.echo(
        f"Wrote {len(cases)} case(s) and {len(cases)} frame(s).\n"
        f"Look at the frames in {frames}, then set expected_phase for each case in "
        f"{out_path} (pre_round, early, mid, post_plant, retake, spectating, unreadable).\n"
        f"Then run: round-review scenes validate {out_path}"
    )


def _hud_reader(config: Config, use_hud: bool) -> object | None:
    """A callable that reads the round clock for a case, or None when it cannot be used."""
    if not use_hud:
        return None
    path = _templates_path(config, None)
    templates = _hud_templates(config, None)
    region = parse_region(config.hud_timer_region)
    runner = SubprocessRunner(config.ffmpeg_path)

    def read(case: SceneCase, recording: Recording) -> HudRead:
        return read_hud(
            recording,
            case.timestamp_s,
            region,
            runner,
            templates,
            out_dir=path.parent / "hud-scenes",
            min_confidence=config.hud_min_confidence,
            threshold=_resolved_threshold(config, recording, region, templates),
        )

    return read


def _run_scene_cases(
    config: Config,
    cases: list[SceneCase],
    frames: int,
    spread_s: float,
    frames_dir: Path,
    use_hud: bool = True,
) -> SceneReport:
    deps: Deps = make_default_deps(config)
    total = len(cases)

    def on_progress(done: int, _total: int) -> None:
        click.echo(f"  [{done}/{total}] scene read", err=True)

    return run_cases(
        cases,
        transport=deps.transport,
        probe=lambda path: probe(path, deps.probe_runner),
        ffmpeg=deps.ffmpeg_runner,
        model=config.model,
        frames_per_case=frames,
        spread_s=spread_s,
        width=config.frame_width,
        frames_dir=frames_dir,
        timeout_s=config.request_timeout_s,
        on_progress=on_progress,
        hud_reader=_hud_reader(config, use_hud),  # type: ignore[arg-type]
        buy_phase_max_s=config.buy_phase_max_s,
        hud_min_confidence=config.hud_min_confidence,
    )


@scenes.command("validate")
@click.argument("labels", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--frames", type=int, default=1, help="Frames per case (1 is the labelled instant).")
@click.option("--spread", "spread_s", type=float, default=1.0, help="Seconds between frames.")
@click.option("--model", default=None, help="Override the configured model for this run.")
@click.option(
    "--json-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the full result as JSON for comparing runs.",
)
@click.option(
    "--min-accuracy",
    type=float,
    default=0.0,
    help="Exit non-zero when phase accuracy is below this (0-1).",
)
@click.option(
    "--hud/--no-hud",
    "use_hud",
    default=True,
    help="Also score the deterministic HUD clock and the corrections it makes.",
)
@click.pass_obj
def scenes_validate(
    config: Config,
    labels: Path,
    frames: int,
    spread_s: float,
    model: str | None,
    json_out: Path | None,
    min_accuracy: float,
    use_hud: bool,
) -> None:
    """Score the situation pass against a labels file."""
    if model:
        config = replace(config, model=model)
    try:
        cases = load_cases(labels)
    except RoundReviewError as exc:
        _fail(exc)
        return
    frames_dir = labels.parent / f"{labels.stem}-run-frames"
    report = _run_scene_cases(config, cases, frames, spread_s, frames_dir, use_hud)
    click.echo(render_scene_report(report))
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
        click.echo(f"\nWrote {json_out}")
    accuracy = max(report.accuracy(), report.hud_accuracy())
    if min_accuracy > 0 and accuracy < min_accuracy:
        click.echo(
            f"\nPhase accuracy {accuracy:.0%} is below the required {min_accuracy:.0%}.", err=True
        )
        sys.exit(1)


@scenes.command("describe")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "timestamps", type=float, multiple=True, required=True, help="Timestamp(s).")
@click.option("--frames", type=int, default=1, help="Frames per timestamp.")
@click.option("--spread", "spread_s", type=float, default=1.0)
@click.option("--model", default=None)
@click.pass_obj
def scenes_describe(
    config: Config,
    file: Path,
    timestamps: tuple[float, ...],
    frames: int,
    spread_s: float,
    model: str | None,
) -> None:
    """Print what the model thinks is on screen at given timestamps. No coaching."""
    if model:
        config = replace(config, model=model)
    cases = [SceneCase(file, t, "unreadable") for t in timestamps]
    frames_dir = file.parent / f"{file.stem}-scene-frames"
    report = _run_scene_cases(config, cases, frames, spread_s, frames_dir)
    for outcome in report.outcomes:
        click.echo(f"\nt={outcome.case.timestamp_s:.1f}s  frame: {outcome.frame}")
        if outcome.error:
            click.echo(f"  error: {outcome.error}")
            continue
        if outcome.situation:
            click.echo(json.dumps(outcome.situation.to_dict(), indent=2))


@main.command()
@click.option("--port", type=int, default=None, help="Loopback port (default: config api_port).")
@click.pass_obj
def serve(config: Config, port: int | None) -> None:
    """Run the local API used by the desktop app. Binds to 127.0.0.1 only."""
    holder = ConfigHolder(config, default_config_path())

    def run_review(
        path: Path, ctx: PlayerContext, options: JobOptions, on_progress: ProgressFn
    ) -> None:
        """One queued review. Settings are read now, so a change saved in the app applies to
        the next queued job without a restart. Per-job coverage overrides apply to this
        review only."""
        live = holder.current
        job_deps = make_default_deps(
            _with_coverage(live, options.coverage, options.max_span_s, options.max_windows)
        )
        review_file(path, job_deps, context=ctx, force=options.force, on_progress=on_progress)

    def run_question(path: Path, ctx: PlayerContext, spec: QuestionSpec) -> dict[str, object]:
        """One queued question about a moment in a clip."""
        return asdict(answer_question(path, make_default_deps(holder.current), spec, context=ctx))

    jobs = JobQueue(run_review, clock=_utc_now, run_question=run_question)
    app = create_app(holder, jobs)
    uvicorn_run(app, host="127.0.0.1", port=port or config.api_port, log_level="info")


@main.group(name="config")
def config_group() -> None:
    """Configuration commands."""


STARTER_CONFIG = """# round-review configuration. Everything here is optional except
# recordings_dir, which `watch` and the desktop app need to find your clips.
# Full reference: docs/requirements.md

{recordings_line}

# Ollama model. qwen3-vl:8b wants about 12 GB of VRAM; use qwen3-vl:4b on smaller cards.
model = "qwen3-vl:8b"
num_ctx = 24576

# How much of each recording to review. "full" tiles the whole clip; "sampled" takes a few
# windows. max_span_s = 60 reviews only the first minute of gameplay.
coverage = "full"
max_span_s = 0
max_windows = 0

# 0 means unlimited. Local inference has no per-call cost and a full review is dozens of calls.
daily_call_cap = 0

# Read the round clock deterministically and veto the model when it misreads the phase.
# Check the region against your own footage with: round-review hud crop <clip> --at 45
hud_check = true
hud_timer_region = "{region}"
"""


@config_group.command("path")
@click.pass_obj
def config_path_command(config: Config) -> None:
    """Print where the configuration file is read from."""
    click.echo(default_config_path())


@config_group.command("init")
@click.option(
    "--recordings-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Your Outplayed output folder.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing configuration file.")
@click.pass_obj
def config_init(config: Config, recordings_dir: Path | None, force: bool) -> None:
    """Write a starter configuration file with comments, ready to edit."""
    target = default_config_path()
    if target.exists() and not force:
        click.echo(f"{target} already exists; edit it, or pass --force to replace it.", err=True)
        sys.exit(1)
    recordings_line = (
        f'recordings_dir = "{recordings_dir.as_posix()}"'
        if recordings_dir
        else '# recordings_dir = "C:/Users/you/Videos/Outplayed/VALORANT"'
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        STARTER_CONFIG.format(recordings_line=recordings_line, region=config.hud_timer_region),
        encoding="utf-8",
    )
    click.echo(f"Wrote {target}")
    if not recordings_dir:
        click.echo("Set recordings_dir in it to your Outplayed folder, then run `config show`.")


@config_group.command("show")
@click.pass_obj
def config_show(config: Config) -> None:
    """Print the effective configuration."""
    for f in fields(config):
        value: Any = getattr(config, f.name)
        click.echo(f"{f.name} = {value}")


@main.group(name="ledger")
def ledger_group() -> None:
    """Ledger commands."""


@ledger_group.command("list")
@click.pass_obj
def ledger_list(config: Config) -> None:
    """List processed recordings."""
    try:
        entries = read_ledger(config.ledger_path)
    except RoundReviewError as exc:
        _fail(exc)
        return
    if not entries:
        click.echo(f"ledger empty ({config.ledger_path})")
        return
    for e in entries:
        when = e.processed_at.isoformat(timespec="minutes")
        tail = e.report_path or e.error or ""
        click.echo(f"{when}  {e.status:<7} calls={e.model_calls:<3} {Path(e.path).name}  {tail}")
