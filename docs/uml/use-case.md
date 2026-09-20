# Use case diagram

Mermaid has no native use-case notation; actors are stick figures rendered as nodes on the left and right, use cases as ellipses, system boundary as a subgraph.

```mermaid
flowchart LR
    Player(["🧍 Player"])
    Outplayed(["📼 Outplayed"])
    Ollama(["🧠 Ollama"])

    subgraph RR["round-review"]
        UC1(["Review a single recording"])
        UC2(["Watch the recordings folder"])
        UC3(["Show effective configuration"])
        UC4(["List processed recordings"])
        UC5(["Detect finished recording"])
        UC6(["Sample frames from windows"])
        UC7(["Request coaching findings"])
        UC8(["Write Markdown report with evidence"])
        UC9(["Enforce daily model-call cap"])
        UC10(["Browse clips with status"])
        UC11(["Queue a clip for analysis"])
        UC12(["Watch analysis progress"])
        UC13(["Play clip with finding markers"])
    end

    Player --> UC1
    Player --> UC2
    Player --> UC3
    Player --> UC4
    Player --> UC10
    Player --> UC11
    Player --> UC12
    Player --> UC13
    UC11 -->|includes| UC1
    UC13 -->|includes| UC8
    Outplayed -.writes MP4.-> UC5
    UC2 -->|includes| UC5
    UC5 -->|includes| UC1
    UC1 -->|includes| UC6
    UC1 -->|includes| UC7
    UC1 -->|includes| UC8
    UC7 -->|includes| UC9
    UC7 --> Ollama
```

## Use case notes

| Use case | Precondition | Main flow | Failure handling |
| --- | --- | --- | --- |
| Review a single recording | File exists, ffmpeg/ffprobe on PATH, Ollama reachable | probe, select windows, extract frames, review each window, write report, append ledger | `VideoError`, `OllamaError`, `ParseError`, `CapExceeded` exit non-zero with the class name; ledger records `failed`/`skipped` |
| Watch the recordings folder | `recordings_dir` configured | poll; when a file is stable and not in the ledger, run "Review a single recording" | per-file errors are logged and recorded; the loop continues; `CapExceeded` pauses model calls until the next day |
| Detect finished recording | none | size and mtime unchanged for `quiet_polls`, mtime older than `min_age_s`, ffprobe succeeds | not yet stable = stay pending |
| Enforce daily model-call cap | ledger readable | count calls in ledger for today, refuse before the transport is invoked | `CapExceeded` |
| Browse clips with status | API running, `recordings_dir` configured | list MP4s, merge job queue state and ledger status | folder missing = empty list with a warning |
| Queue a clip for analysis | clip exists in the folder | POST job; single worker runs "Review a single recording" | duplicate submit returns the existing job |
| Watch analysis progress | job exists | poll job; progress = windows done / total | failed job shows the error class and message |
| Play clip with finding markers | report.json exists, clip is H.264 | video element streams the MP4; markers at finding timestamps; click seeks and shows the finding card | HEVC clip = explicit unsupported-codec message |
