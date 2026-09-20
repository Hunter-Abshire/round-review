# round-review

Local, offline coaching for recorded Valorant gameplay. Watches your Outplayed (Overwolf) recordings folder, samples frames from finished clips with ffmpeg, asks a local Ollama vision model for timestamped findings, and writes a Markdown report with evidence screenshots. Nothing leaves your machine.

Status: pipeline, CLI, local API and Electron desktop app all run end to end against a fake model in tests and boot on macOS. Coaching quality with a real model is unvalidated. Try `review` on a few clips before trusting anything.

## Overview

```
Outplayed writes match.mp4
        |
        v
round-review watch  -- polls the folder, waits until the file stops changing
        |
        v
ffprobe -> pick 3 x 12 s windows -> ffmpeg extracts 1 fps JPEGs
        |
        v
Ollama /api/chat (qwen3-vl:8b) with the frames -> JSON findings
        |
        v
reports/<clip>_<key>/report.md + frames/   and a line in ledger.jsonl
```

Requirements and diagrams: `docs/requirements.md`, `docs/uml/`.

## Install & Run Locally

Needs Python 3.12, ffmpeg and ffprobe on PATH, and Ollama with a vision model pulled.

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

ollama pull qwen3-vl:8b        # 12 GB+ VRAM; use qwen3-vl:4b on smaller cards
```

Config lives in the user data dir (`%LOCALAPPDATA%\round-review\config.toml` on Windows, `~/Library/Application Support/round-review/config.toml` on macOS). Everything has a default except `recordings_dir`, which `watch` needs:

```toml
recordings_dir = "C:/Users/you/Videos/Outplayed/VALORANT"
model = "qwen3-vl:8b"
windows_per_file = 3
fps = 1.0
daily_call_cap = 30
```

Any key can be overridden with `ROUND_REVIEW_<KEY>` in the environment.

```
round-review config show
round-review review "C:/Users/you/Videos/Outplayed/VALORANT/clip.mp4" --context "Gold 2, Jett, Ascent"
round-review watch
round-review ledger list
```

`review` exits non-zero with the error class name (`OllamaError: ...`, `VideoError: ...`) on failure. A file already in the ledger is refused unless you pass `--force`.

## Desktop app

`desktop/` is an Electron shell. It starts `round-review serve` from the repo `.venv` as a sidecar on a free loopback port, lists the clips in `recordings_dir` with their status, queues analyses one at a time with progress, and opens a finished review as the clip playing in a video element with one clickable marker per finding underneath. Clicking a marker seeks the video and shows the finding: what you could see, what you knew, what you couldn't have known, assumptions, the alternative, and the evidence frame.

```
cd desktop
npm install
npm test
env -u ELECTRON_RUN_AS_NODE npm start     # the env var is set by VS Code terminals and breaks Electron
```

Clips must be H.264 for playback; Outplayed set to HEVC records fine but the player shows an unsupported-codec message.

## Debugger

Run the CLI module directly with `-v` for debug logging:

```
.venv/bin/python -m round_review.cli -v review path/to/clip.mp4
```

For a breakpoint inside the pipeline, set one in `src/round_review/pipeline.py` and run the same command under `python -m pdb`, or use the VS Code "Python: Module" launch config with module `round_review.cli`.

## Logging

Standard `logging`, root logger `round_review`, INFO by default and DEBUG with `-v`. The watcher logs each poll decision at DEBUG and each review outcome at INFO/ERROR. Nothing is written to a log file; redirect stdout/stderr if you run `watch` as a background task.

## Tests

```
.venv/bin/pytest                 # 128 tests, generates its own 5 s MP4 with ffmpeg
.venv/bin/pytest -m "not integration"
.venv/bin/ruff check . && .venv/bin/mypy
```

No test talks to Ollama. The model is a fake transport with canned JSON.

```
cd desktop && npm test && npm run typecheck && npm run lint
```
