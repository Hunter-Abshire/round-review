# Deployment diagram

Solid nodes exist in 0.1. Dashed nodes are the planned distribution path (GitHub Actions in the personal AWS account building a downloadable Windows bundle). Nothing in 0.1 depends on the dashed nodes.

```mermaid
flowchart TB
    subgraph PC["Gaming PC (Windows 10/11)"]
        direction TB
        Valorant["Valorant"]
        Outplayed["Outplayed (Overwolf)"]
        RecDir[("Recordings folder<br/>*.mp4")]
        subgraph Desktop["round-review desktop (Electron)"]
            Main["main process<br/>spawns sidecar, owns window"]
            Renderer["renderer<br/>clip list, player, marker track"]
        end
        subgraph RRProc["round-review API sidecar (Python 3.12)"]
            CLI["cli: review / watch / serve"]
            API["FastAPI on 127.0.0.1:8765"]
            Jobs["single worker thread"]
            Pipeline["pipeline"]
        end
        FF["ffmpeg / ffprobe"]
        subgraph OllamaSrv["Ollama server :11434"]
            Model["qwen3-vl:8b"]
        end
        GPU["GPU (12 GB+ VRAM)"]
        DataDir[("User data dir<br/>config.toml, ledger.jsonl, reports/")]
    end

    subgraph Dev["Developer Mac (macOS)"]
        Tests["pytest with fake transport<br/>ffmpeg-generated fixture"]
    end

    subgraph GH["GitHub (Hunter-Abshire/round-review, private)"]
        Repo["main branch"]
        GHA["GitHub Actions"]:::future
    end

    subgraph AWS["Personal AWS account"]
        S3["S3 release bucket"]:::future
        CF["CloudFront download URL"]:::future
    end

    Valorant --> Outplayed --> RecDir
    RecDir --> Pipeline
    CLI --> Pipeline
    Main -->|spawn| CLI
    Renderer -->|HTTP JSON + Range video| API
    API --> Jobs --> Pipeline
    Pipeline -->|subprocess| FF
    Pipeline -->|HTTP JSON| OllamaSrv
    Model --> GPU
    Pipeline --> DataDir
    Tests --> Repo
    Repo -.-> GHA -.->|PyInstaller build| S3 -.-> CF -.->|download| PC

    classDef future stroke-dasharray: 5 5,fill:none
```

## Node notes

| Node | Detail |
| --- | --- |
| round-review API sidecar | The same Python package, started by the Electron main process as `round-review serve`. One worker thread runs reviews sequentially. Loopback only. |
| round-review desktop | Electron; `contextIsolation` on. Renderer talks only to the sidecar over HTTP. Dev: uses the repo `.venv`; packaged: PyInstaller binary next to the app. |
| ffmpeg / ffprobe | Required on PATH or set via `ffmpeg_path` / `ffprobe_path`. Bundled with the future installer. |
| Ollama server | Installed separately by the player. Must have the configured model pulled. |
| User data dir | Resolved with `platformdirs` (`%LOCALAPPDATA%\round-review` on Windows, `~/Library/Application Support/round-review` on macOS). |
| GitHub Actions / S3 / CloudFront | Not built. Kept here so the CLI and packaging layout stay compatible. |
