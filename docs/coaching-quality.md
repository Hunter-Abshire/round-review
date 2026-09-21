# Coaching quality: validation and next steps

The product should explain consequential decisions immediately before or during an
encounter: what was visible, why a position or action mattered, and one practical
alternative. New players need plain-language explanations across combat, positioning,
game sense and abilities. A quota of checklist violations is not useful coaching.

## Observed on the Windows PC (2026-09-21)

- A 749-second recording was sampled at three 12-second windows: 36 seconds total.
- The first sample showed buy phase with a knife. Three aim findings at the same
  timestamp overlapped on the desktop timeline.
- Two other windows lost findings because one-decimal frame captions rounded below
  the actual window start. Captions now map back to the precise sampled timestamp.
- The local `qwen3-vl:4b` model with a 24,576-token context misread the scene: it
  confused player/area labels with agent/map names, and after prompt changes it
  classified live play and spectating as pre-round. Visual inspection of saved frames
  contradicted those classifications. Larger context fixed capacity, not accuracy.
- Relevance gates removed the buy-phase criticism, but the live re-review returned
  zero findings because all three windows were classified as pre-round. This is
  **not a successful coaching-quality validation**.

## Implemented safeguards

Detected pre-round, spectating and unreadable phases abstain before coaching. Phase
inappropriate checks and crosshair criticism without a known equipped firearm are
filtered. These are conservative checks on model output, not independent vision
verification; they can miss useful coaching if the scene is misclassified. Disabling
the situation pass also disables these situation-dependent checks.

The desktop shows sampled coverage, review notes, a separate list for overlapping
findings, and the suggested alternative above the evidence image. `player_notes`
provides persistent beginner preferences for both automatic and requested reviews.

## Scene-recognition validation (implemented 2026-09-21)

Scene recognition can now be measured instead of guessed at:

```
round-review scenes scaffold <clip> --every 30 --out scene-labels.json
# look at the frames it saved, write the phase you actually see into each case
round-review scenes validate scene-labels.json --json-out run-8b-1frame.json
round-review scenes validate scene-labels.json --model qwen3-vl:4b --frames 3 --json-out run-4b-3frame.json
```

`validate` runs only the situation pass, so no coach calls are spent, and prints
accuracy per expected phase, the most common confusions, and every failing case with
the path to the frame that produced it. It warns explicitly when the model answers the
same phase for every case, which scores well on a skewed label set while recognising
nothing. `--min-accuracy` exits non-zero so a run can gate a change. `scenes describe`
prints the raw situation JSON at chosen timestamps for quick spot checks.

The labelled set is the missing input: build it from real clips covering buy phase,
safe travel, live preparation, active fight, death and spectating, then compare models
and frame counts on it before trusting any coaching output.

## Full-video coverage (implemented 2026-09-21)

Reviews now tile the whole recording by default instead of sampling three windows.
`max_span_s` reviews only the first N seconds, `max_windows` keeps a capped budget
spread evenly across the clip rather than stopping early, and `daily_call_cap = 0`
(the new default) means unlimited because local inference has no per-call cost. A cap
or a transport failure part-way through keeps the windows already reviewed and records
the review as `partial`, so an hour of work is never lost to one failure.

## Next implementation priorities

1. Build the labelled validation set with `scenes scaffold` and run `scenes validate`
   against `qwen3-vl:8b` and `qwen3-vl:4b`, at one and three frames per case. Treat the
   accuracy numbers as the gate for everything below; do not assume either change
   improves accuracy.
2. Replace evenly spaced selection with encounter timestamps. Prefer recorded
   player kill/death/damage events where accessible; otherwise use a validated local
   detector or player-selected timestamps. The MP4 folder currently has no event
   sidecars; an Outplayed event reader is not implemented.
3. Include roughly 10–20 seconds before contact and a short aftermath, in bounded
   batches that fit the context. Keep own-player actions separate from spectating,
   merge nearby events, and distribute the review budget across encounters.
4. Require specific evidence of the opportunity or risk behind each suggestion,
   avoid hindsight, and combine repeated mistakes into a small set of useful habits.
   Evaluate advice against the labelled clips, not the number of generated findings.

Until encounter selection and scene recognition pass those checks, full-match
coaching remains experimental. Short clips centred on encounters are a better input
for testing than a whole match with three arbitrary samples.
