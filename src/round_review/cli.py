"""click entry point. Thin: parses arguments, wires default deps, maps errors to exit codes."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import click
import uvicorn

from round_review.coaching.context import PlayerContext, context_from_mapping
from round_review.config import Config, default_config_path, default_data_dir, load_config
from round_review.errors import RoundReviewError
from round_review.ledger import is_processed, read_ledger, recording_key
from round_review.pipeline import Deps, make_default_deps, review_file
from round_review.server.app import create_app
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
from round_review.vision.digits import DigitTemplates
from round_review.vision.hud import HudRead, Region, learn_from_crop, parse_region, read_hud
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
        samples = learn_from_crop(
            recording,
            timestamp_s,
            box,
            SubprocessRunner(config.ffmpeg_path),
            path.parent / "hud-learn",
            reads,
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
        templates = DigitTemplates.load(path)
        recording = probe(file, SubprocessRunner(config.ffprobe_path))
    except RoundReviewError as exc:
        _fail(exc)
        return
    if not templates.characters():
        click.echo(f"No digit templates in {path} yet; run `hud learn` first.", err=True)
    for timestamp in timestamps:
        read = read_hud(
            recording,
            timestamp,
            box,
            SubprocessRunner(config.ffmpeg_path),
            templates,
            out_dir=path.parent / "hud-read",
            min_confidence=config.hud_min_confidence,
        )
        click.echo(f"\nt={timestamp:.1f}s  crop: {read.crop_path}")
        if read.error:
            click.echo(f"  error: {read.error}")
            continue
        if read.clock_text is None:
            click.echo(
                f"  found {read.glyph_count} glyph(s) but could not read them. "
                "Teach these shapes with `hud learn --at "
                f"{timestamp:.1f} --reads <what you see>`."
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
    templates = DigitTemplates.load(path)
    if not templates.characters():
        click.echo(f"No digit templates in {path}; scoring the model only.", err=True)
        return None
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
    deps: Deps = make_default_deps(config)

    def run_review(
        path: Path, ctx: PlayerContext, options: JobOptions, on_progress: ProgressFn
    ) -> None:
        """One queued review. Per-job coverage overrides apply to this review only."""
        job_deps = replace(
            deps,
            config=_with_coverage(
                config, options.coverage, options.max_span_s, options.max_windows
            ),
        )
        review_file(path, job_deps, context=ctx, force=options.force, on_progress=on_progress)

    jobs = JobQueue(run_review, clock=deps.clock)
    app = create_app(config, jobs)
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
