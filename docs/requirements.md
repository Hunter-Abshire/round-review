# round-review requirements

Version 1.1 (review layout, drawings, clip identity). Last updated 2026-09-22.

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
| FR-20 | A review covers the whole recording by default: contiguous windows tile the usable span. `coverage = "sampled"` takes a few evenly spread windows instead, `max_span_s` reviews only the first N seconds of gameplay, and `max_windows` caps the budget while keeping the windows spread across the whole clip. |
| FR-21 | A cap or a failure part-way through a review keeps the windows already reviewed, writes the report, and records the ledger status as `partial`. A failure before any window completes still fails the file. |
| FR-22 | `round-review scenes scaffold/validate/describe` score the situation pass against hand-labelled moments without spending coach calls, reporting accuracy per phase, a confusion matrix, failing cases with their frames, and a warning when the model answers the same phase every time. |
| FR-23 | The desktop app can re-review a finished, partial or failed clip (`force`), choose how much of a clip to review per job, and shows which stretches of the recording were reviewed as bands on the timeline with one numbered pin per finding. |
| FR-24 | A review that abstained on most of its windows says so in plain words, counts the reasons, and points at scene validation, instead of being indistinguishable from a review of flawless play. Reports carry `partial` and `stopped_reason`. |
| FR-25 | The round clock is read deterministically, with no model involved: ffmpeg crops the timer region to a grayscale raster and the digits are segmented and matched against templates learned from the player's own footage. Reading a HUD never fails a review; an unreadable HUD is simply no evidence. |
| FR-26 | A clock above `buy_phase_max_s` (default 45 s, above both the buy phase and the post-plant spike timer) proves the round is live and pre-plant, and overrides a model phase of `pre_round`, `post_plant`, `retake` or unreadable. `spectating` is never overridden, because the clock belongs to whoever is being watched. Any override is recorded in the report. |
| FR-51 | A finding or strength may carry focus shapes (box, point, arrow) in fractions of the frame with a label. Shapes outside the frame or without a label are dropped, and never cost the finding they came with. |
| FR-52 | The app draws those shapes over the video, mapped to the letterboxed picture rather than the element, so they land correctly whatever the clip's aspect ratio. |
| FR-53 | The review view is a video and a tabbed sidebar (findings, coach, ask). Opening a finding shows it in the sidebar with previous and next, so reading a finding never means leaving the video. Findings are grouped under one heading per category. |
| FR-54 | Following playback highlights a finding without opening it, so the video cannot hijack what the player is reading. |
| FR-55 | A finished review records what the clip was (agent, map, side), taking the most common answer across windows. The played date comes from the recorder's filename, falling back to the file's own time. An unreviewed clip shows no identity rather than a guess. |
| FR-56 | Trade-distance findings are dropped when no enemy was on screen: being far from a teammate while crossing an empty map is not an untradeable death. |
| FR-57 | The empty-review diagnosis fires only when the coached windows produced little. A review that skipped most of a match but produced findings says what was skipped without calling itself broken. |
| FR-46 | Every editable setting carries a group, label, plain-language help, type, bounds, unit and where its choices come from, so a settings screen can render all of them without knowing what any of them mean. Derived or write-once paths are explicitly excluded. |
| FR-47 | Settings are readable and writable over the API. A write validates before touching the file, so an invalid value changes nothing, and the file is rewritten grouped and commented. |
| FR-48 | A saved setting applies to the next queued job without restarting the app. |
| FR-49 | The app reports which models Ollama has pulled as the choices for the model setting, and flags any setting an environment variable has taken over, since the file would not win. |
| FR-50 | The desktop app has a settings screen under File, reachable by menu, shortcut and toolbar button, showing advanced settings only on request and keeping unsaved edits when a save fails. |
| FR-42 | An asked question retrieves reference passages from the whole bundled knowledge base (one per agent, map, check and category drill) plus the sections of any files in `notes_dir`, ranked by keyword relevance with no embedding model, vector store or network access. |
| FR-43 | Passages about the agent and map in play are favoured, but a favoured passage can never outrank relevance: tags reorder matches, they do not create them. |
| FR-44 | The prompt states that notes are the player's own and correct about their setups, and that reference material describes the game rather than these frames. |
| FR-45 | Every answer carries the titles of the passages it was given, shown in the app, so an answer can be audited against what it read. A notes folder that cannot be read costs the references, never the answer. |
| FR-38 | The player can ask a free-text question about a chosen time range of a clip, or about a single moment, which widens to a few seconds either side. The range is clamped to the recording and to `max_question_span_s`. |
| FR-39 | An answer gives the answer first, then what was visible, then what only became clear later, then its assumptions, then up to three alternatives each with a reason. It may state that the frames do not support an answer rather than guessing. |
| FR-40 | Questions are queued on the same single worker as reviews, so a question never competes with a review for the GPU, and are never deduplicated. |
| FR-41 | The review view has an ask box prefilled from the playhead with suggested questions, and lists answers newest first, each linking back to the moment it was about. |
| FR-31 | Each window may report up to two strengths: specific, evidence-bound things the player did right. Praise that is vague, filler, or shorter than a phrase is dropped rather than shown, and zero strengths is a valid result. |
| FR-32 | Findings sharing a checklist id are merged into one habit carrying every instance and timestamp. Habits are ranked by recurrence and by what the category of mistake costs, and the action set is capped at three. |
| FR-33 | The report is ordered verdict, corrections, strengths, hindsight-dependent findings, everything else, practice. Corrections precede praise deliberately. |
| FR-34 | Findings that depend on information revealed after the decision are reported in their own section rather than counted against the player. |
| FR-35 | Every review ends with exactly one in-game rule, one drill and a success check, chosen for the top-ranked habit from `knowledge/drills.json`. |
| FR-36 | Every report states what it could not see: the sampled coverage, the absence of per-round outcome data, and that it reads neither comms nor intent. |
| FR-37 | The desktop review view opens with the coach panel, with clickable jumps to every instance of a habit, and groups the findings sidebar by category. |
| FR-28 | The scene-reading pass carries `situation_frames` frames spread across the window rather than every extracted frame, because it runs for every window including the ones it skips. Requests keep the model resident between calls. |
| FR-29 | Reviews record their window count and duration in the ledger, and each clip shows an estimated review time derived from what past reviews on this machine actually took. |
| FR-30 | A review that skips `abstain_streak_limit` windows in a row stops, is recorded as partial, and names scene validation as the thing to check. |
| FR-27 | `round-review hud crop/learn/read` verify the timer region against real footage, teach the digit shapes, and confirm what the clock proves. `scenes validate` scores the HUD clock alongside the model and counts the corrections it makes. The app warns when the HUD check is enabled but untrained, since it would otherwise do nothing silently. |
| FR-20 | With the situation pass enabled, a detected pre-round, spectating or unreadable phase skips coaching with an explicit note. A parsed finding whose checklist category conflicts with the detected phase is dropped. Crosshair criticism requires a known equipped weapon other than knife, melee, spike or ability. These conservative window-level gates depend on the situation model and do not replace encounter detection. |
| FR-21 | Coaching prioritizes supported decisions immediately before or during encounters, explaining the practical risk and one action in plain language for beginners. Buy phase, safe travel, and absent context must not become a quota of mistakes. An empty, filtered review is a valid result; only actual parse failures count toward all-windows-unparseable failure. |
| FR-22 | Desktop shows sampled duration versus recording duration, review notes, and a selectable list of all findings. Findings at the same timestamp remain individually accessible and selection survives a playback update at that timestamp. The suggested alternative appears before the evidence image. |
| FR-15 | Structured requests send `think: false`. If a completed response (`done=true`, `done_reason=stop`) has empty content and a whole JSON object with the schema's required root keys in `message.thinking`, log a compatibility warning and pass it through normal finding validation. Never substitute prose, incomplete output, or thinking when content is present. |

## Anti-hindsight requirements

| ID | Requirement |
| --- | --- |
| AH-1 | The prompt states the player only sees what is on screen at each frame and forbids judging an earlier decision with information from a later frame. |
| AH-2 | Every finding must separately state `visible_evidence`, `information_available_to_player`, and `information_revealed_later`. |
| AH-3 | Every finding lists `assumption_flags`: anything the model guessed rather than saw (enemy positions, cooldowns, economy). |
| AH-4 | If a finding's alternative depends on later-revealed information, the parser downgrades confidence and the report shows a warning. |
| AH-5 | Findings whose timestamp lies outside their window are rejected. A timestamp exactly matching a frame's one-decimal caption is first mapped back to that frame's precise time; other out-of-window times are still rejected. At most 4 findings per window. |
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
- Minimap and kill-feed reading (only the round clock is read deterministically)
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
| `daily_call_cap` | 0 | 0 means unlimited; a full review is dozens of calls |
| `situation_pass` | true | Run the describe-first pass before coaching |
| `situation_frames` | 3 | Frames the scene pass carries; 0 = all of them |
| `ollama_keep_alive` | 30m | Keep the model resident between calls |
| `abstain_streak_limit` | 20 | Give up after this many skipped windows in a row; 0 = never |
| `coach_frames` | 0 | Frames the coach pass carries; 0 = all of them |
| `question_frames` | 6 | Frames one asked question carries |
| `max_question_span_s` | 60 | Longest stretch one question may cover |
| `notes_dir` | none | Folder of your own markdown or text notes, searched for questions |
| `reference_passages` | 4 | Passages one question may carry; 0 switches retrieval off |
| `max_reference_chars` | 4000 | Size budget for retrieved passages |
| `coverage` | full | `full` tiles the whole recording; `sampled` takes `windows_per_file` windows |
| `hud_check` | true | Read the round clock deterministically and veto phase misreads |
| `hud_timer_region` | 0.455,0.020,0.090,0.055 | Timer box as fractions of the frame; verify with `hud crop` |
| `hud_templates_path` | `<user data dir>/hud-digits.json` | Digit shapes learned from your own footage |
| `hud_min_confidence` | 0.8 | Glyph match quality below which a clock read is not trusted |
| `buy_phase_max_s` | 45 | A clock above this can only be a live, pre-plant round |
| `max_span_s` | 0 | 0 = whole recording; otherwise the first N seconds of gameplay |
| `max_windows` | 0 | 0 = unlimited; otherwise a capped budget spread across the clip |
| `player_notes` | empty | Default notes for every review, including the watcher; explicit per-review notes override them. |
| `num_ctx` | 16384 | Ollama context window; the coach prompt is ~2-5k tokens plus images |
| `poll_s` | 20 | Watcher poll interval |
| `quiet_polls` | 3 | Consecutive unchanged polls before a file is stable |
| `min_age_s` | 120 | Minimum mtime age before a file is stable |
| `request_timeout_s` | 900 | Ollama request timeout; a coach call with a dozen frames is slow |
| `api_port` | 8765 | Loopback port for the local API used by the desktop app |

## Known risks

- 12 frames at 1280 px per call may exceed model context or take substantial time on the target GPU. `num_ctx` defaults to 16,384; increase it if inputs plus the response do not fit, or reduce `fps` / `frame_width`. Larger contexts use more memory; measure on the target PC.
- Outplayed writes MP4 progressively. Whether it writes to a temp name and renames is unverified; the stability rule in FR-2 covers both cases.
- ffmpeg `-ss` before `-i` is keyframe-approximate. Timestamps may be off by up to a GOP; acceptable for 12 s windows.
- Coaching quality from a general vision model is unproven. Validate on real clips against a human coach before trusting `watch` mode.
- Browser playback needs H.264/AAC. Outplayed can be set to HEVC (H.265), which Chromium will not decode; the UI must show a clear error rather than a black player. ffprobe already reports the codec.
- Electron packaging must bundle a PyInstaller build of the Python API plus ffmpeg/ffprobe; the dev flow uses the repo `.venv`.

### HUD brightness calibration

`hud_threshold` defaults to -1 (adaptive) and accepts integers 0 through 255 for a
fixed grayscale cutoff. Learning and every reading path (CLI, scene validation,
and reviews) use the same configured threshold. Invalid values are configuration
errors. Recalibrate learned glyphs after changing the region or threshold.
