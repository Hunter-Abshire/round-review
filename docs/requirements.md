# round-review requirements

Version 0.3 (coaching knowledge). Last updated 2026-09-20.

## Purpose

A local, offline tool that reviews recorded Valorant gameplay and produces timestamped coaching findings. It picks up finished recordings written by Outplayed (Overwolf) on the player's Windows PC, samples frames with ffmpeg, sends them to a local Ollama vision model, and writes a Markdown report with evidence screenshots. No cloud services, no telemetry, no cost beyond electricity.

Valorant is the first supported game profile. The pipeline is game-agnostic; only the prompt and future HUD parsing are game-specific.

## Actors

| Actor | Role |
| --- | --- |
| Player | Records matches, runs the CLI, reads reports |
| Outplayed | Writes MP4 recordings to a folder on disk |
| round-review watcher | Long-running process that detects finished recordings and reviews them |
| Ollama | Local HTTP inference server hosting a vision model |

## Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Watch a configured recordings directory (recursively) for new `.mp4` files. |
| FR-2 | Treat a file as finished only when its size and mtime are unchanged across N consecutive polls, the mtime is older than a minimum age, and ffprobe returns a duration. Files still being written are left pending, not failed. |
| FR-3 | Select a configurable number of fixed-length windows (default 3 x 12 s) from each recording, skipping the first and last 30 s. Selection is deterministic for a given duration. |
| FR-4 | Extract frames per window with ffmpeg at a configurable rate and width (default 1 fps, 1280 px), as JPEGs on disk. |
| FR-5 | Send each window's frames, in order and captioned with their offset, to the configured Ollama model and require a JSON response matching the finding schema. Every request, including retries, sends the configured context size as `options.num_ctx`. |
| FR-6 | Write one Markdown report per recording with, per finding: timestamp, category, observation, what was visible, what the player could know, what was only revealed later, assumptions the model made, suggested alternative, confidence, and a linked evidence frame. |
| FR-7 | Record every processed file in an append-only ledger (status ok / failed / skipped, model call count, report path, error). Never re-review a file already in the ledger. Enforce a daily cap on model calls; retries count. |
| FR-8 | CLI commands: `review <file>`, `watch <dir>`, `config show`, `ledger list`. Non-zero exit with the error class name on failure. |
| FR-9 | All configuration from a TOML file in the user config dir with `ROUND_REVIEW_*` environment overrides. No hardcoded paths, URLs, or model names in code. |
| FR-10 | Alongside the Markdown, write `report.json`: the same findings in a machine-readable form with evidence frames as relative paths. |
| FR-11 | A local HTTP API (bound to 127.0.0.1 only) lists clips in the recordings folder with their status (new / queued / running / done / failed / skipped), enqueues reviews, reports job progress (windows done of total), serves `report.json`, and streams the MP4 with HTTP Range support plus evidence JPEGs. |
| FR-12 | Reviews requested from the UI run on a single background worker, one at a time, in submission order. Submitting a clip that is already queued or running returns the existing job. |
| FR-13 | Desktop app (Electron) starts the Python API as a sidecar, shows the clip list with status, lets the player click Analyze, shows progress, and opens a finished review. |
| FR-14 | The review view plays the clip in an HTML5 video element with a marker track underneath: one marker per finding at its timestamp. Clicking a marker seeks the video and shows that finding (observation, what you could see, what you couldn't have known, assumptions, alternative, confidence, evidence frame). |
| FR-15 | A bundled coaching knowledge base (`src/round_review/coaching/knowledge/`): a review checklist of concrete, frame-observable checks grouped by category with ids; a brief per agent (role, abilities, job in round, common mistakes, HUD ability checks); a brief per map (sites, callouts, defaults, setups, power positions, common mistakes, utility notes). Loaded and validated at startup; malformed data is a `KnowledgeError`. |
| FR-16 | Each window is reviewed in two passes. Pass 1 (situation) asks the model to describe only what is visible: agent, map, side, phase, weapon, abilities available, credits, teammates alive, enemies visible, a per-frame timeline and a summary. Pass 2 (coach) receives the player context, rank priorities, the agent and map briefs, the situation read and the checklist, and must cite a checklist id on every finding. Pass 1 can be disabled (`situation_pass = false`) to halve model calls. |
| FR-17 | Player context (rank, agent, map, side, focus, notes) can be supplied by the player via CLI options or the desktop context bar. Values the player supplies win; the situation pass fills in the rest. Blank means auto-detect. |
| FR-18 | Every finding's evidence frame is re-cut from the recording at the finding's exact timestamp, not the nearest sampled frame. If that cut fails the sampled frame is kept and a warning is recorded. |
| FR-19 | The checklist sent to the coach pass is filtered by the detected round phase (no post-plant checks pre-round, and so on) so the prompt fits next to the images. Unknown phase sends everything. |
| FR-15 | Structured requests send `think: false`. If a completed response (`done=true`, `done_reason=stop`) has empty content and a whole JSON object with the schema's required root keys in `message.thinking`, log a compatibility warning and pass it through normal finding validation. Never substitute prose, incomplete output, or thinking when content is present. |

## Anti-hindsight requirements

| ID | Requirement |
| --- | --- |
| AH-1 | The prompt states the player only sees what is on screen at each frame and forbids judging an earlier decision with information from a later frame. |
| AH-2 | Every finding must separately state `visible_evidence`, `information_available_to_player`, and `information_revealed_later`. |
| AH-3 | Every finding lists `assumption_flags`: anything the model guessed rather than saw (enemy positions, cooldowns, economy). |
| AH-4 | If a finding's alternative depends on later-revealed information, the parser downgrades confidence and the report shows a warning. |
| AH-5 | Findings whose timestamp lies outside their window are rejected. At most 4 findings per window. |
| AH-6 | A finding that cites an unknown checklist id is kept but relabelled `other` with a warning, so invented principles are visible in the report. |

## Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-1 | Fully offline. Only network call is to the local Ollama URL. |
| NFR-2 | Runs on Windows 10/11 (target) and macOS (development). Paths handled with `pathlib`; subprocesses use argument lists, never a shell. |
| NFR-3 | Test suite runs without Ollama or a GPU. The video fixture is generated by ffmpeg at test time. |
| NFR-4 | Every external dependency (ffmpeg, ffprobe, Ollama HTTP, clock, filesystem stat) is injected so each module is unit-testable in isolation. |
| NFR-5 | No error is swallowed. Failures are logged with context and recorded in the ledger, or re-raised to the CLI. |
| NFR-6 | A crash mid-review never loses a written report: the ledger entry is the last write. |
| NFR-7 | Python 3.12, type hints on every signature, `mypy --strict` and `ruff` clean. |
| NFR-8 | Desktop code is TypeScript with `strict: true`, jest tests for every pure module, ESLint + Prettier clean. Electron renderer runs with `contextIsolation` on and `nodeIntegration` off; all Node access goes through the preload bridge. |
| NFR-9 | The API only ever binds to loopback. No authentication is added because nothing else can reach it; do not change the bind address without adding auth. |

## Out of scope for 0.1

- Audio analysis
- Whole-match review (only sampled windows)
- Overwolf game-events integration (round start, kills, deaths) for event-anchored sampling
- HUD / minimap OCR
- Installer, auto-update, code signing (the Electron shell exists; packaging does not)
- Cross-match habit tracking
- AWS CI/CD and downloadable build (planned, see deployment diagram)

## Configuration keys

| Key | Default | Notes |
| --- | --- | --- |
| `recordings_dir` | none, required for `watch` | Outplayed output folder |
| `reports_dir` | `<user data dir>/reports` | |
| `ledger_path` | `<user data dir>/ledger.jsonl` | |
| `ffmpeg_path` / `ffprobe_path` | `ffmpeg` / `ffprobe` | Override on Windows if not on PATH |
| `ollama_url` | `http://localhost:11434` | |
| `model` | `qwen3-vl:8b` | Sized for 12 GB+ VRAM; `qwen3-vl:4b` for smaller cards |
| `num_ctx` | 16384 | Positive integer context size in tokens, sent to Ollama on every request; override with `ROUND_REVIEW_NUM_CTX`. Budget for image inputs and the response. |
| `window_s` | 12 | |
| `windows_per_file` | 3 | |
| `fps` | 1.0 | Frames per second sampled inside a window |
| `frame_width` | 1280 | |
| `daily_call_cap` | 60 | Two calls per window with the situation pass on |
| `situation_pass` | true | Run the describe-first pass before coaching |
| `num_ctx` | 16384 | Ollama context window; the coach prompt is ~2-5k tokens plus images |
| `poll_s` | 20 | Watcher poll interval |
| `quiet_polls` | 3 | Consecutive unchanged polls before a file is stable |
| `min_age_s` | 120 | Minimum mtime age before a file is stable |
| `request_timeout_s` | 300 | Ollama request timeout |
| `api_port` | 8765 | Loopback port for the local API used by the desktop app |

## Known risks

- 12 frames at 1280 px per call may exceed model context or take substantial time on the target GPU. `num_ctx` defaults to 16,384; increase it if inputs plus the response do not fit, or reduce `fps` / `frame_width`. Larger contexts use more memory; measure on the target PC.
- Outplayed writes MP4 progressively. Whether it writes to a temp name and renames is unverified; the stability rule in FR-2 covers both cases.
- ffmpeg `-ss` before `-i` is keyframe-approximate. Timestamps may be off by up to a GOP; acceptable for 12 s windows.
- Coaching quality from a general vision model is unproven. Validate on real clips against a human coach before trusting `watch` mode.
- Browser playback needs H.264/AAC. Outplayed can be set to HEVC (H.265), which Chromium will not decode; the UI must show a clear error rather than a black player. ffprobe already reports the codec.
- Electron packaging must bundle a PyInstaller build of the Python API plus ffmpeg/ffprobe; the dev flow uses the repo `.venv`.
