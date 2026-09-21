# round-review

Local, offline coaching for recorded Valorant gameplay. Watches your Outplayed (Overwolf) recordings folder, samples frames from finished clips with ffmpeg, asks a local Ollama vision model for timestamped findings, and writes a Markdown report with evidence screenshots. Nothing leaves your machine.

Status: pipeline, CLI, local API and Electron desktop app all run end to end against a fake model in tests and boot on macOS. **Scene recognition with a small local model is not yet validated** and coaching quality depends on it, so start with `round-review scenes validate` before trusting any findings. See `docs/coaching-quality.md`.

## Overview

```
Outplayed writes match.mp4
        |
        v
round-review watch  -- polls the folder, waits until the file stops changing
        |
        v
ffprobe -> tile the whole clip into 12 s windows -> ffmpeg extracts 1 fps JPEGs
        |
        v
Ollama /api/chat (qwen3-vl:8b): pass 1 reads the scene, pass 2 coaches -> JSON findings
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
num_ctx = 16384
windows_per_file = 3
fps = 1.0
daily_call_cap = 30
```

Any key can be overridden with `ROUND_REVIEW_<KEY>` in the environment.

`num_ctx` sets the Ollama context size in tokens for every request, including retries.
The default is 16,384: sampled images can exceed Ollama's 4,096-token default.
If a request still exceeds the context, increase `num_ctx` (allowing room for the response),
or reduce `fps` / `frame_width`. Larger contexts require more memory.
Restart the desktop app and watcher after changing configuration. Retry an already-recorded
failure with `review <file> --force`; it remains in the ledger until explicitly re-reviewed.

Structured requests disable thinking. If Ollama returns a completed JSON object in
`message.thinking` with empty `message.content`, the app logs a compatibility warning
and validates that object through the normal finding parser. Prose and truncated
responses are not accepted through this fallback.

```
round-review config show
round-review review "C:/Users/you/Videos/Outplayed/VALORANT/clip.mp4" --context "Gold 2, Jett, Ascent"
round-review watch
round-review ledger list
```

`review` exits non-zero with the error class name (`OllamaError: ...`, `VideoError: ...`) on failure. A file already in the ledger is refused unless you pass `--force`. Add `--coverage sampled`, `--first 60` or `--max-windows 10` to bound a review.

## Teach it to read the clock (do this once)

A vision model will sometimes call live play "buy phase", and a review then skips every
window and tells you nothing. The round timer settles it without a model: above 45 seconds
the round must be live, because neither the buy phase nor the post-plant spike timer ever
shows more than that. Teach the digits once from your own footage and every review gains a
veto over that misread.

```
round-review hud crop <clip> --at 45                  # is the box on the timer?
round-review hud learn <clip> --at 45 --reads 1:39    # repeat until nothing is missing
round-review hud read <clip> --at 45                  # confirm, and see what it proves
```

If the crop is not showing the timer, adjust `hud_timer_region` (x,y,w,h as fractions of
the frame) and try again. Set `hud_check = false` to turn the whole thing off.

## Check the model can read the screen

A vision model that misreads the scene produces useless coaching, so measure that first:

```
round-review scenes scaffold path/to/clip.mp4 --every 30
# open the frames it saved, write the phase you actually see into scene-labels.json
round-review scenes validate scene-labels.json --json-out run1.json
round-review scenes validate scene-labels.json --model qwen3-vl:4b --frames 3 --json-out run2.json
```

This spends no coaching calls. It prints accuracy per phase, the most common confusions,
and every failing case with the frame that produced it. Once the clock reader is trained it
also scores the model with and without the clock, so you can see how much the veto buys you.

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
.venv/bin/pytest                 # generates its own 5 s MP4 with ffmpeg; no Ollama needed
.venv/bin/pytest -m "not integration"
.venv/bin/ruff check . && .venv/bin/mypy
```

No test talks to Ollama. The model is a fake transport with canned JSON.

```
cd desktop && npm test && npm run typecheck && npm run lint
```
