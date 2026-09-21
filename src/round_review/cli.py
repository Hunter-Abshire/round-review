"""click entry point. Thin: parses arguments, wires default deps, maps errors to exit codes."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import click
import uvicorn

from round_review.coaching.context import PlayerContext, context_from_mapping
from round_review.config import Config, load_config
from round_review.errors import RoundReviewError
from round_review.ledger import is_processed, read_ledger, recording_key
from round_review.pipeline import Deps, make_default_deps, review_file
from round_review.server.app import create_app
from round_review.server.jobs import JobQueue
from round_review.video.probe import probe
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
    force: bool,
) -> None:
    """Review one recording and write its report."""
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


@main.command()
@click.option("--port", type=int, default=None, help="Loopback port (default: config api_port).")
@click.pass_obj
def serve(config: Config, port: int | None) -> None:
    """Run the local API used by the desktop app. Binds to 127.0.0.1 only."""
    deps: Deps = make_default_deps(config)
    jobs = JobQueue(
        lambda path, ctx, on_progress: review_file(
            path, deps, context=ctx, on_progress=on_progress
        ),
        clock=deps.clock,
    )
    app = create_app(config, jobs)
    uvicorn_run(app, host="127.0.0.1", port=port or config.api_port, log_level="info")


@main.group(name="config")
def config_group() -> None:
    """Configuration commands."""


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
