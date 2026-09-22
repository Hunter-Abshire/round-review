# round-review

Local coaching for your own Valorant recordings. It picks up finished clips from your
Outplayed folder, samples frames with ffmpeg, asks a vision model running on your own
machine for timestamped findings, and shows you each one against the moment it happened.

Nothing is uploaded. There is no account and no API key. The only network traffic is to
Ollama on `localhost`.

**Read this first.** Coaching quality depends entirely on whether the local model can read
your screen, and on a small model it often cannot: it will call live gameplay "buy phase"
and then skip the round. The setup below has you check that before you rely on any advice.
Steps 6 and 7 are not optional garnish; they are how you find out whether this works on
your hardware. Background in [docs/coaching-quality.md](docs/coaching-quality.md).

---

## What you need

| | Why | Check it with |
| --- | --- | --- |
| Windows 10/11 | Where Valorant and Outplayed run. macOS works for everything except recording. | |
| A GPU with 8 GB+ VRAM | Runs the vision model. 12 GB+ for the better one. | `nvidia-smi` |
| Python 3.12 or newer | The review engine | `python --version` |
| Node.js 20 or newer | Only for the desktop app | `node --version` |
| ffmpeg and ffprobe on PATH | Frame extraction and HUD reading | `ffmpeg -version` |
| Ollama | Runs the vision model locally | `ollama --version` |
| Outplayed (Overwolf) | Records your matches | |

Installing the missing pieces on Windows:

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
winget install Ollama.Ollama
```

Close and reopen your terminal afterwards so PATH updates. On macOS use
`brew install python@3.12 node ffmpeg ollama` instead.

---

## Setup

### 1. Get the code and install it

```powershell
git clone https://github.com/Hunter-Abshire/round-review.git
cd round-review
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

On macOS the last two lines are `python3 -m venv .venv` and
`.venv/bin/pip install -e ".[dev]"`.

Every command below starts with `.venv\Scripts\round-review` on Windows or
`.venv/bin/round-review` on macOS. Activate the virtual environment
(`.venv\Scripts\Activate.ps1`) and you can just type `round-review`.

Check it installed:

```powershell
round-review --help
```

### 2. Pull the vision model

```powershell
ollama pull qwen3-vl:8b
```

Use `qwen3-vl:4b` if you have 8 GB of VRAM or less, but expect worse scene reading. Confirm
Ollama is up and holding the model:

```powershell
ollama list
curl http://localhost:11434/api/tags
```

### 3. Find your recordings folder

In Outplayed: **Settings → Capture → Storage**. It is usually
`C:\Users\<you>\Videos\Outplayed\VALORANT`. Copy that path.

While you are in there, set the **encoder to H.264**, not HEVC. HEVC clips review fine but
will not play back inside the desktop app.

### 4. Create your config file

The quickest way is the app: launch it (step 8) and open **File → Settings**, which lists
every setting with an explanation and writes the file for you. From the terminal:

```powershell
round-review config init --recordings-dir "C:/Users/you/Videos/Outplayed/VALORANT"
round-review config show
```

`config init` writes a commented starter file; `config path` tells you where it lives
(`%LOCALAPPDATA%\round-review\config.toml` on Windows). Use forward slashes in the path, or
double the backslashes, because the file is TOML.

`config show` prints every effective setting. If `recordings_dir` reads `None`, the path did
not take.

### 5. Review one clip, bounded

Do not start with a full 12 minute match. Time one minute first:

```powershell
round-review review "C:/path/to/clip.mp4" --first 60 --rank "Gold 2"
```

This reviews the first minute of gameplay, roughly five windows. Watch how long a window
takes: a full 12 minute match is about 57 of them, so multiply accordingly. After your first
completed review the app puts that estimate on each clip card for you.

Expect this to be slow on a small card. Every window costs one call to work out what is on
screen, plus a second call to coach it if it is worth coaching, and each call carries several
frames. If it is slower than you can live with, drop `situation_frames` to 1 or use
`--coverage sampled`.

You should see a report path printed at the end. If you get `OllamaError: cannot reach
Ollama`, Ollama is not running. If you get zero findings and a warning about the model
misreading the screen, that is the known problem and steps 6 and 7 are how you deal with it.

### 6. Teach it to read the round clock

This is the fix for the model calling live play "buy phase". The round timer settles the
question without a model: above 45 seconds the round must be live, because neither the buy
phase nor the post-plant spike timer ever shows more than that. Teach the digits once and
every review from then on can overrule that misread.

```powershell
round-review hud crop "C:/path/to/clip.mp4" --at 45
```

**Open `hud-timer.png` and look at it.** It should contain the round timer and almost nothing
else. The default region was estimated, not measured on your resolution, so it may well be
off. If it is, edit `hud_timer_region` in your config (`x,y,w,h` as fractions of the frame,
so `0.455,0.020,0.090,0.055` means 45.5% across, 2% down, 9% wide, 5.5% tall) and crop again
until the picture is right.

If a clean-looking crop reports too few glyphs, the bright background may be joining
the digits. Set `hud_threshold = 220` in config as a starting point and verify it on
your footage. The default `-1` chooses an adaptive cutoff; fixed values range from 0
to 255. Relearn the templates after changing this setting or the crop.

Then teach it the digits. Pick timestamps where you can read the clock yourself, and tell it
what you see:

```powershell
round-review hud learn "C:/path/to/clip.mp4" --at 45 --reads 1:39
round-review hud learn "C:/path/to/clip.mp4" --at 70 --reads 1:02
round-review hud learn "C:/path/to/clip.mp4" --at 95 --reads 0:47
```

After each one it lists which digits are still missing. Keep going until nothing is. Then
confirm:

```powershell
round-review hud read "C:/path/to/clip.mp4" --at 45
```

It should print the clock, a confidence near 100%, and "live round, so any buy phase or
post-plant call is wrong".

### 7. Check the model can read your screen

Now measure the thing that actually determines whether the coaching is worth anything.

```powershell
round-review scenes scaffold "C:/path/to/clip.mp4" --every 30
```

That saves a frame every 30 seconds and writes `scene-labels.json` with a blank
`expected_phase` for each. Open the frames, and for each one write what you actually see:

| Label | When |
| --- | --- |
| `pre_round` | Buy phase, behind the barrier |
| `early` | Round just started, moving out |
| `mid` | Round in progress |
| `post_plant` | Spike is down, you are defending it |
| `retake` | Spike is down, you are attacking it |
| `spectating` | You are dead and watching someone else |
| `unreadable` | You genuinely cannot tell |

Then score it:

```powershell
round-review scenes validate scene-labels.json --json-out run-8b.json
```

This spends no coaching calls. It prints accuracy per phase, the most common confusions, and
every failing case with the frame that caused it. It also scores the model with and without
the clock reader, so you can see how much step 6 bought you.

**How to read the result.** If it warns that the model answered the same phase for every
case, the model is not reading the scene at all and the accuracy number means nothing. Try
`--model qwen3-vl:8b` if you were on the 4b, or `--frames 3` to give it motion. Until this
number is respectable, treat every finding as unverified.

### 8. Run the desktop app

```powershell
cd desktop
npm install
npm start
```

`npm install` downloads Electron, about 100 MB, once. `npm start` builds and launches. The
app starts the review engine itself on a loopback port; you do not run `serve` separately.

In the app: pick how much to review, hit **Analyze** on a clip, watch the window counter, then
**Open review**. You can also ask about a specific moment: pause where you want, press **Ask
about this moment** or right-click the timeline, pick a suggested question or type your own,
and it answers that one question about that stretch of footage with a couple of alternatives
you could have chosen instead. The timeline under the video shades the stretches that were reviewed and puts
a numbered pin on each finding. Click a pin to jump there. Shift plus the arrow keys steps
between findings.

If you are launching from a VS Code terminal, Electron will fail with
`Cannot read properties of undefined (reading 'whenReady')`. That terminal sets
`ELECTRON_RUN_AS_NODE`. Clear it first: `Remove-Item Env:ELECTRON_RUN_AS_NODE` in PowerShell,
or `env -u ELECTRON_RUN_AS_NODE npm start` on macOS.

---

## Settings

**File → Settings** in the app, or `Ctrl+,`. Everything is there: which model to use (a list
of what Ollama has actually pulled), whether to review the whole clip or the first minute,
how many frames each pass carries, the round-clock reader, your notes folder. Each setting
says what it does, and saving applies to your next review without restarting.

Advanced settings are hidden behind a checkbox. If an environment variable has taken a
setting over, the app disables it and tells you, rather than letting a saved value silently
do nothing.

The file is still there at `%LOCALAPPDATA%\round-review\config.toml` and you can edit it by
hand; the app keeps whatever you put in it.

## Day to day

```powershell
round-review review <clip>                 # whole clip
round-review review <clip> --first 120     # first two minutes only
round-review review <clip> --coverage sampled   # a few windows, fastest
round-review review <clip> --force         # review it again
round-review watch                         # review new recordings as they appear
round-review ledger list                   # what has been reviewed
```

Add `--rank`, `--agent`, `--map`, `--side` or `--focus` to any review. Anything you leave out
the model tries to read off the HUD. `--rank` is worth setting: it changes which coaching
priorities apply.

Reports are written to `%LOCALAPPDATA%\round-review\reports\<clip>_<key>\`, containing
`report.md` to read, `report.json` for the app, and `frames/` with the evidence images. Every
review appends a line to `ledger.jsonl`.

---

## When something goes wrong

| What you see | What it means |
| --- | --- |
| `OllamaError: cannot reach Ollama` | Ollama is not running. Start it and check `ollama list`. |
| Zero findings, warning about misreading the screen | The model called the footage buy phase or spectating. Do steps 6 and 7. |
| `daily model-call cap reached` | Set `daily_call_cap = 0` in your config. Local calls cost nothing. |
| Review stopped early, report says partial | A cap or a failure interrupted it. The findings so far are kept; **Re-analyze** runs it again. |
| Timer check is on but untrained | `hud_check = true` with no digits learned does nothing. Do step 6, or set `hud_check = false`. |
| Findings are empty or truncated with a big clip | Raise `num_ctx` to 32768, or lower `frame_width` to 960 or `fps` to 0.5. |
| Video will not play in the app | The clip is HEVC. Findings still work; switch Outplayed to H.264 for playback. |
| `binary not found on PATH` | ffmpeg or ffprobe is not installed, or set `ffmpeg_path` and `ffprobe_path` in your config. |
| A review takes forever | Every window costs a scene-reading call whether or not it gets coached. Use `--first` or `--coverage sampled`, or lower `situation_frames` to 1. The clip card shows an estimate once you have completed one review. |
| Review stopped after N windows in a row were skipped | The model is skipping everything, so it gave up rather than spend hours proving it. Do steps 6 and 7. Set `abstain_streak_limit = 0` to review the whole thing anyway. |

---

## How it works

```
Outplayed writes match.mp4
        |
        v
watcher polls the folder until the file stops changing
        |
        v
ffprobe -> tile the whole clip into 12 s windows -> ffmpeg extracts 1 fps JPEGs
        |
        +--> ffmpeg crops the round timer -> digits read without a model
        |         (a clock above 45 s proves the round is live)
        v
Ollama, twice per window: pass 1 reads the scene, pass 2 coaches against a checklist
        |
        v
report.md + report.json + frames/, and a line in ledger.jsonl
```

Each finding has to cite a checklist item, say what was visible, separate what you knew from
what only became clear later, and name its assumptions. That is deliberate: it is what stops
the model telling you that you should have known about an enemy you could not possibly see.

The report itself is shaped like a coach's write-up rather than a list of everything the
model noticed: one verdict, at most three prioritised fixes with every timestamp they
occurred at, then what you did well, then one in-game rule and one drill for your next
session. Repeats of the same mistake are merged into a single habit so three symptoms of one
problem do not eat all three slots. Findings that only make sense with hindsight are put in
their own section instead of counted against you, and every report states what it could not
see. Those choices come from coaching-feedback research; the sources are in
[docs/coaching-quality.md](docs/coaching-quality.md).

The coaching knowledge lives in JSON under `src/round_review/coaching/knowledge/`: a checklist
of 87 concrete checks, a brief for each of the 29 agents, and one for each of the 13 maps.
Edit those files to change what the coach looks for; there is no need to touch the prompt.

Requirements and diagrams: [docs/requirements.md](docs/requirements.md) and
[docs/uml/](docs/uml/). Known quality problems and what is being done about them:
[docs/coaching-quality.md](docs/coaching-quality.md).

---

## Working on it

With the virtual environment activated:

```
pytest                       # generates its own test video with ffmpeg
pytest -m "not integration"  # skips the tests that need ffmpeg
ruff check . && mypy
cd desktop && npm test && npm run typecheck && npm run lint
```

No test contacts Ollama; the model is always a fake transport with canned responses. Run
`round-review --help` or any subcommand with `--help` for the full option list. Contributor
notes are in [AGENTS.md](AGENTS.md).
