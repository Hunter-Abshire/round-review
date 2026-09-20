# Class diagram

Data is immutable (`@dataclass(frozen=True, slots=True)`). External systems sit behind `Protocol`s so tests inject fakes. There are no service classes; each module exposes pure functions that take their dependencies as arguments. `Deps` bundles the injected dependencies for the pipeline.

```mermaid
classDiagram
    direction LR

    class Config {
        +Path recordings_dir
        +Path reports_dir
        +Path ledger_path
        +str ffmpeg_path
        +str ffprobe_path
        +str ollama_url
        +str model
        +float window_s
        +int windows_per_file
        +float fps
        +int frame_width
        +int daily_call_cap
        +float poll_s
        +int quiet_polls
        +float min_age_s
        +float request_timeout_s
    }

    class Recording {
        +Path path
        +float duration_s
        +float fps
        +int width
        +int height
        +int size_bytes
        +float mtime
    }

    class Window {
        +int index
        +float start_s
        +float end_s
        +str source
    }

    class FrameSample {
        +int window_index
        +float timestamp_s
        +Path path
    }

    class Finding {
        +float timestamp_s
        +str category
        +str observation
        +str visible_evidence
        +str information_available_to_player
        +str information_revealed_later
        +tuple~str~ assumption_flags
        +str suggested_alternative
        +float confidence
        +Path evidence_frame
    }

    class WindowResult {
        +Window window
        +tuple~FrameSample~ samples
        +tuple~Finding~ findings
        +int model_calls
        +tuple~str~ warnings
    }

    class Report {
        +Recording recording
        +datetime generated_at
        +str model
        +tuple~WindowResult~ results
        +tuple~str~ warnings
    }

    class LedgerEntry {
        +str key
        +str path
        +datetime processed_at
        +int model_calls
        +str report_path
        +str status
        +str error
    }

    class ChatRequest {
        +str model
        +str system
        +str prompt
        +tuple~str~ images_b64
        +dict format
        +float timeout_s
    }

    class ChatResponse {
        +str content
        +int prompt_eval_count
        +int eval_count
    }

    class FfprobeRunner {
        <<Protocol>>
        +run(args) str
    }
    class FfmpegRunner {
        <<Protocol>>
        +run(args) None
    }
    class Transport {
        <<Protocol>>
        +chat(request: ChatRequest) ChatResponse
    }
    class UrllibTransport {
        +str base_url
        +chat(request) ChatResponse
    }
    class SubprocessRunner {
        +str executable
        +run(args) str
    }

    class Deps {
        +Config config
        +FfprobeRunner probe_runner
        +FfmpegRunner ffmpeg_runner
        +Transport transport
        +Callable clock
    }

    class RoundReviewError
    class ConfigError
    class VideoError
    class OllamaError
    class ParseError
    class CapExceeded
    class LedgerError
    class AlreadyProcessed

    Transport <|.. UrllibTransport
    FfprobeRunner <|.. SubprocessRunner
    FfmpegRunner <|.. SubprocessRunner
    Deps o-- Config
    Deps o-- FfprobeRunner
    Deps o-- FfmpegRunner
    Deps o-- Transport
    Report o-- Recording
    Report o-- WindowResult
    WindowResult o-- Window
    WindowResult o-- FrameSample
    WindowResult o-- Finding
    Finding --> FrameSample : evidence_frame
    Transport ..> ChatRequest
    Transport ..> ChatResponse
    RoundReviewError <|-- ConfigError
    RoundReviewError <|-- VideoError
    RoundReviewError <|-- OllamaError
    RoundReviewError <|-- ParseError
    RoundReviewError <|-- CapExceeded
    RoundReviewError <|-- LedgerError
    RoundReviewError <|-- AlreadyProcessed
```

## Module map

| Module | Owns | Key functions |
| --- | --- | --- |
| `config` | `Config` | `load_config(path, env)`, `default_config_path()` |
| `ledger` | `LedgerEntry` | `read_ledger`, `append_entry`, `is_processed`, `calls_today`, `recording_key` |
| `video.probe` | `Recording`, `FfprobeRunner` | `probe(path, runner)`, `parse_probe_json` |
| `video.windows` | `Window` | `select_windows(duration_s, window_s, count, edge_skip_s)` |
| `video.frames` | `FrameSample`, `FfmpegRunner` | `build_extract_args`, `extract_frames`, `encode_frame_b64` |
| `llm.transport` | `ChatRequest`, `ChatResponse`, `Transport`, `UrllibTransport` | `UrllibTransport.chat` |
| `llm.client` | | `build_chat_request`, `send_review(request, transport, calls_today, cap)` |
| `coaching.prompt` | `SYSTEM_PROMPT`, `FINDING_SCHEMA` | `build_user_prompt(window, samples)` |
| `coaching.parse` | `Finding` | `parse_findings(text, window)`, `extract_json(text)` |
| `coaching.review` | `WindowResult` | `review_window(window, samples, deps, calls_so_far)` |
| `report.markdown` | `Report` | `render_report`, `write_report` |
| `pipeline` | `Deps` | `review_file(path, deps)`, `make_default_deps(config)` |
| `watcher` | `FileSnapshot`, `WatchState` | `poll_once`, `is_stable`, `watch_loop` |
| `cli` | | `main` (click group) |
| `errors` | exception hierarchy | |
