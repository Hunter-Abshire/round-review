# round-review

Local, offline coaching for recorded Valorant gameplay. Watches your Outplayed recordings folder, samples frames from finished clips with ffmpeg, asks a local Ollama vision model for timestamped findings, and writes a Markdown report with evidence screenshots.

Status: early development. Not yet usable end to end.

## Overview

See `docs/requirements.md` and the diagrams in `docs/uml/`.

## Install & Run Locally

Requires Python 3.12, ffmpeg and ffprobe on PATH, and (for real reviews) a running Ollama with `qwen3-vl:8b` pulled.

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
