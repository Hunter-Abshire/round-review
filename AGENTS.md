# round-review

Instructions for coding agents (Codex and others) working in this repository. `.claude/CLAUDE.md` is the Claude Code equivalent; keep the two in sync when behaviour or commands change.

## What this is

Local, offline coaching tool for recorded Valorant gameplay. It picks up finished Outplayed (Overwolf) MP4 recordings, samples frames with ffmpeg, sends them to a local Ollama vision model, and writes a Markdown + JSON report of timestamped findings with evidence screenshots. An Electron desktop app (`desktop/`) lists clips, queues analyses, and plays a clip with one marker per finding under the video. Personal project by Hunter-Abshire. Windows is the runtime target, macOS is the dev machine. No cloud, no telemetry, no network calls except to the local Ollama server.

Two toolchains:

- Python 3.12 package in `src/round_review/`: pipeline, folder watcher, click CLI, loopback FastAPI (`round-review serve`).
- TypeScript in `desktop/`: Electron main / preload / renderer. The desktop app never touches the filesystem or ffmpeg itself; it starts `round-review serve` as a sidecar and talks HTTP on 127.0.0.1.

Requirements live in `docs/requirements.md`; UML (use-case, class, sequence, deployment) in `docs/uml/` as Mermaid. Update both when behaviour changes.

## Setup and commands

Python:

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                          # full suite; integration tests need ffmpeg/ffprobe on PATH
.venv/bin/pytest -m "not integration"     # unit only
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/mypy                            # strict, src/ only
.venv/bin/round-review --help             # review, watch, serve, scenes, config show, ledger list
.venv/bin/round-review scenes --help      # scene-recognition validation (scaffold, validate, describe)
.venv/bin/round-review hud --help         # deterministic round-clock reading (crop, learn, read)
```

Desktop:

```
cd desktop
npm install                               # downloads Electron (~100 MB); needs network
npm test                                  # jest via ts-jest; renderer tests use @jest-environment jsdom docblocks
npm run typecheck && npm run lint         # tsc strict (main + renderer), ESLint + Prettier
npm run build                             # tsc for main/preload, esbuild bundle for renderer, copy static
env -u ELECTRON_RUN_AS_NODE npm start     # run the app against the repo .venv sidecar
```

Definition of done for any change: pytest, ruff, mypy, jest, typecheck and lint all clean, docs updated if behaviour changed, and this file plus `.claude/CLAUDE.md` updated if commands or patterns changed.

## Sandbox notes

- Everything except `npm install` and the Electron download works offline. Tests never contact Ollama or the internet.
- Ollama is not installed on the dev machine. Never add a test that needs a real model; use the `fake_transport` fixture (Python) or a fake `fetch` (TypeScript).
- `ELECTRON_RUN_AS_NODE=1` is set in VS Code-style terminals and makes `require('electron')` return a path string (`app` is undefined). Launch Electron with `env -u ELECTRON_RUN_AS_NODE`.
- macOS has no `timeout` binary; background the process and kill it instead.
- Do not commit or push unless asked. Commits use Conventional Commits (`feat`, `fix`, `chore`, `refactor`, `docs`, `test`) with an optional scope, no ticket IDs.
- Keep generated dependencies, build output, caches, recordings, and local logs out of Git. Track `desktop/package.json` and `desktop/package-lock.json`; install dependencies locally instead of committing `node_modules`.

## How to work here

- Test-driven. Write the failing test first (`tests/` mirrors `src/`; `desktop/test/` mirrors `desktop/src/`), then the smallest code that passes it. Never commit code without a test that exercised it.
- Functional style. Pure functions with injected dependencies. Python: `Protocol`s for ffmpeg, ffprobe, Ollama transport, clock, stat; frozen dataclasses for data; no service classes (the one stateful class is `server.jobs.JobQueue`). TypeScript: `strict: true`, no `any`, `as const` maps instead of enums, pure reducer and render functions, `app.ts` is the only file that wires `fetch`, `<video>` and timers.
- Errors are never swallowed. Python raises one of the classes in `src/round_review/errors.py`; the pipeline records them in the ledger and the CLI exits non-zero with the class name. The job worker logs and records failures and keeps running.
- Config comes from a TOML file in the user data dir plus `ROUND_REVIEW_*` env overrides. Do not hardcode paths, URLs, ports, or model names.
- Naming: Python snake_case; TypeScript camelCase for code, snake_case for anything payload-shaped (the API JSON, `desktop/src/shared/types.ts`). Prettier: single quotes, `arrowParens: 'avoid'`, 2 spaces, semicolons.
- Comment the why, not the what. JSDoc/docstrings only on exported or tricky functions.

## Layout

| Path | Contents |
| --- | --- |
| `src/round_review/` | `config`, `ledger`, `watcher`, `pipeline`, `cli`, `errors`, `video/` (probe, windows, frames), `llm/` (transport, client), `coaching/` (prompt, parse, review), `report/` (markdown, json_report), `server/` (jobs, app) |
| `tests/` | Mirrors `src/`; `conftest.py` builds the ffmpeg fixture |
| `desktop/src/main/` | Electron main process and sidecar launcher |
| `desktop/src/preload/` | Context bridge exposing only the API base URL |
| `desktop/src/renderer/` | `state.ts` reducer, `timeline.ts` marker math, `dom.ts` render functions, `api.ts` client, `app.ts` wiring, `index.html`, `styles.css` |
| `desktop/src/shared/` | Types mirroring the API JSON |
| `desktop/test/` | jest specs mirroring `desktop/src/` |
| `docs/` | Requirements and Mermaid UML |

## Patterns that must survive edits

- **Dependency injection by argument.** `video.probe.CommandRunner` (ffprobe/ffmpeg), `llm.transport.Transport` (Ollama) and the `opener` on `UrllibTransport` are passed in. Tests use `FakeRunner` / `FakeTransport` classes next to the tests; production wiring happens once in `pipeline.make_default_deps`.
- **Daily cap is checked before the network.** `llm.client.send_review` raises `CapExceeded` when `calls_today >= cap`; retries count as calls. Never call a transport directly from coaching code.
- **Context size is explicit.** `Config.num_ctx` (default 16384, positive integer) is injected into `UrllibTransport` by `make_default_deps` and sent as `options.num_ctx` on every request, including retries. Do not rely on Ollama's server default for multi-image prompts.
- **Structured output compatibility is narrow.** Send `think=false` with structured requests. Only when content is empty and generation completed normally may a whole JSON object in `message.thinking` with the schema's required root keys be passed to normal finding validation. Log this fallback; never use reasoning prose or truncated output as findings.
- **Parse is lenient on format, strict on content.** `coaching.parse.extract_json` slices the first `{` to the last `}`; missing required fields raise `ParseError`; findings outside the window, over the 4-per-window limit, or with confidence out of range become warnings.
- **The round clock is read deterministically, and it outranks the model on one point.** `vision/` crops the timer with ffmpeg (`format=gray`, 4x upscale), parses the PGM in pure Python, segments glyphs and matches them against templates learned from the player's own footage (`hud learn`). `constrain_phase` makes exactly one inference, because it is the only one the clock supports alone: a clock above `buy_phase_max_s` (45 s, above both the buy phase and the spike timer) proves the round is live and pre-plant, so `pre_round`, `post_plant`, `retake` and unreadable are corrected. `spectating` is never overridden. Reading a HUD never raises into a review: an unreadable crop is a `HudRead` carrying an error. Do not add further inferences without evidence that the clock supports them.
- **HUD brightness is calibrated consistently.** `hud_threshold` is -1 for adaptive or 0..255 for a fixed grayscale cutoff. The same setting must reach learning, CLI reads, scene validation and pipeline reads. Relearn templates after changing the cutoff or crop; bright HUD backgrounds can merge digits under the adaptive midpoint.
- **An untrained HUD check does nothing.** `read_hud` is skipped when no digits are learned, and `/api/settings` exposes `hud_ready` so the app can say so. Keep that visible; a silent no-op is worse than the feature being off.
- **A review that abstains everywhere diagnoses itself.** `diagnosis.abstention_warning` counts the abstention reasons and says the model is probably misreading the screen. `WindowResult.abstained_reason` and `hud_override` are the machine-readable side of that; keep them set.
- **Asking about a moment is the same two passes over a chosen range.** `pipeline.answer_question` clamps the range with `question.clamp_span` (a zero-length range is a click and widens to a few seconds), reads the scene, then answers. It offers up to three alternatives with reasons rather than one instruction, because a choice of solutions is what makes corrective feedback land, and it may answer that the frames do not support an answer. Nothing is skipped for being the buy phase: the player chose the moment.
- **Questions share the review worker.** `JobQueue.submit_question` puts them on the same single thread so they never compete for the GPU, and unlike reviews they are never deduplicated.
- **The report is shaped by coaching research, not by what the model emits.** `coaching/session.py` merges findings that share a checklist id into one habit, ranks them `3*recurrence + 3*cost + 2*control + upstream` (weights in `COST_WEIGHTS`), caps the action set at three, puts corrections before praise, separates hindsight-dependent findings, and ends with one rule and one drill from `knowledge/drills.json`. Every one of those choices has a source in `docs/coaching-quality.md`; do not change a number here without reading that section, and do not add a fourth focus item.
- **Praise is filtered, never padded.** `parse_strengths` drops praise under 25 characters or matching a filler phrase, and zero strengths is a valid review. There is no praise-to-criticism ratio to hit; the famous 5:1 comes from a withdrawn model.
- **Checklist text is model-facing.** It is phrased as a question for the model and reads terribly as a heading, so the report titles habits with `session.first_sentence(observation)` instead. Keep the checklist question out of player-facing output.
- **The scene pass is where a review's time goes.** It runs for every window, including the ones it then skips, so its images dominate cost: twelve 1280px frames per window is tens of thousands of image tokens before any coaching. `coaching.frames.select_situation_frames` trims it to `situation_frames` (3) spread across the window; the coach pass still gets everything. Requests set `keep_alive` so the model is not reloaded between dozens of back-to-back calls. Measure a change with the ledger's `duration_s` and `windows`, not by guessing.
- **A review that keeps skipping gives up.** `abstain_streak_limit` consecutive abstentions stop the run with `ReviewAbandoned`, recorded as partial and pointing at `scenes validate`. `pacing.estimate_seconds` turns past ledger timings into the estimate shown on each clip card. Keep both: the failure mode they guard against cost two hours of wall clock to discover.
- **A review covers the whole recording by default.** `video.windows.plan_windows` is the only entry the pipeline uses: `coverage="full"` tiles the usable span, `"sampled"` spreads `windows_per_file` windows. `max_span_s` reviews the first N seconds of gameplay (measured after the intro skip), `max_windows` caps the budget but keeps the windows spread across the whole clip rather than stopping early. `daily_call_cap = 0` means unlimited, which is the default: local inference has no per-call cost and a 12-minute clip is ~57 windows.
- **Partial reviews are saved, not lost.** A cap or transport failure after at least one window keeps the results, writes the report and records the ledger status `partial`; only a failure before any window completes raises. Ledger statuses are `ok`, `partial`, `failed`, `skipped`, and the API maps `partial` to a clip that can be both opened and re-analyzed.
- **Scene recognition is validated separately from coaching.** `round-review scenes scaffold/validate/describe` (in `validation/scenes.py`) run only the situation pass against hand-labelled moments and score it: accuracy per phase, a confusion matrix, failing cases with the frame that produced them, and an explicit warning when the model answers the same phase for every case. Fix scene recognition before judging coaching output. See `docs/coaching-quality.md`.
- **Re-review is a job option.** `JobOptions` carries `force` plus per-job coverage overrides from the API to `review_file`; the desktop Re-analyze button sets `force: true`. Config is never mutated, `dataclasses.replace` builds the per-job `Config`.
- **Coaching knowledge is data.** `src/round_review/coaching/knowledge/{checklist,agents,maps}.json` are validated by `parse_checklist`/`parse_agents`/`parse_maps` (raise `KnowledgeError`) and cached by `load_knowledge()`. Check ids are `<category>.<slug>`; the coach must cite one per finding and unknown ids become `other` with a warning. Edit the JSON, not the prompt, to change what the coach looks for. Keep strings short: they go into the context window.
- **Two passes per window.** `coaching.review.review_window` first asks for a `Situation` (agent, map, side, phase, HUD state, timeline) with `SITUATION_SCHEMA`; a failed situation pass is a warning. The coach pass then gets `PlayerContext` (user values win over detected), rank priorities, agent and map briefs, the situation and a checklist filtered by phase (`relevant_categories`). `situation_pass = false` in config drops pass 1. Calls per window: 2 (+1 retry).
- **Relevance before volume.** With the situation pass enabled, detected buy-phase, spectating or unreadable-phase windows skip coaching with a note. Filter phase-inappropriate checklist ids and crosshair criticism without a known equipped firearm. Unknown or noncombat scenes are not a quota of mistakes. Restore exact sample timestamps from rounded captions before bounds validation. Empty filtered reviews are valid; only caught ParseErrors count toward an all-windows-unparseable failure.
- **Evidence frames are exact.** `pipeline._attach_exact_evidence` re-cuts `frames/eWW_NN.jpg` at each finding's timestamp with `extract_single_frame`; failure keeps the sampled `wWW_NNN.jpg` and warns. The API frame regex accepts both names.
- **Anti-hindsight is enforced in code.** A finding with non-placeholder `information_revealed_later` and confidence above 0.7 is downgraded to 0.5 with a warning. Keep in sync with `docs/requirements.md` AH-1..AH-6.
- **One retry.** `coaching.review.review_window` re-asks once with `RETRY_NUDGE` on `ParseError`, then raises a `ParseError` carrying `model_calls`. `pipeline.review_file` turns one bad window into a warning and fails the file only when every window is unparseable.
- **Ledger is the last write.** Report first, then the ledger line. Status `ok` / `failed` (VideoError, OllamaError, ParseError) / `skipped` (CapExceeded). A file already in the ledger raises `AlreadyProcessed` unless `force=True`. Failed Ollama calls are counted against the cap.
- **Watcher is polling, not inotify.** `watcher.poll_once` is pure given `stat_fn` and `now`; a file is ready after `quiet_polls` unchanged sightings (first sighting counts as 1) and `min_age_s` of mtime age. The loop probes before reviewing, attempts each file once per session, and pauses on `CapExceeded` until the date changes.
- **CLI is thin.** `cli.py` parses, wires deps, maps `RoundReviewError` to exit 1 with the class name. Tests monkeypatch `cli.load_config`, `cli.review_file`, `cli.watch_loop`, `cli.uvicorn_run`.
- **API is loopback-only and unauthenticated.** `serve` binds to 127.0.0.1. Keys are validated with a 16-hex regex and frame names with `w\d\d_\d\d\d.jpg` before touching the filesystem; job submission rejects paths outside `recordings_dir`. Do not change the bind address without adding auth.
- **Jobs run one at a time.** `JobQueue` owns the worker thread and returns snapshots; `submit` dedups queued/running jobs by key. Clip status precedence in `/api/clips`: active job > latest job > ledger > `new`.
- **Progress is a callback.** `review_file(on_progress=)` reports `(done, total)` windows; the renderer polls `/api/jobs/{id}` every 2 s.
- **Sidecar paths join with the target platform's separator** (`path.win32` / `path.posix`) so Windows commands are testable on macOS. Dev uses `<repo>/.venv/bin/round-review`; packaged will use `process.resourcesPath/round-review(.exe)`, which does not exist yet.
- ffmpeg: `-ss` before `-i` (fast, keyframe-approximate). Frame files are `w{window:02d}_{n:03d}.jpg`; timestamps are `start + n/fps`.

## Testing conventions

- Fakes over mocks: small classes with a `calls` list; no `unittest.mock`, no `jest.mock`.
- `tests/conftest.py` generates a 5 s colour-bar MP4 with ffmpeg once per session. Tests needing it are marked `integration` and skip when ffmpeg is absent.
- Every module has a matching test file: `tests/<layer>/test_<module>.py`, `desktop/test/<layer>/<module>.spec.ts`.

## Status (2026-09-20)

Pipeline + CLI (128 pytest) and desktop app (29 jest) complete and smoke-tested on macOS. Hunter has run it on the Windows PC with qwen3-vl:8b (2026-09-20): output was thin and generic, which drove the knowledge base + two-pass work. Next: live validation on the Windows gaming PC with `qwen3-vl:8b` (latency, JSON contract, advice quality, confirm clips are H.264 since HEVC will not play in the app); tune `fps` / `frame_width` / `num_ctx`; Overwolf game-events to anchor windows (`video.windows` has a `source="events"` slot); packaging (PyInstaller sidecar + electron-builder + GitHub Actions release).
