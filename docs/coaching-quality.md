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

## Confirmed again on the Windows PC (2026-09-21, second run)

A 12:29 recording reviewed with `qwen3-vl:4b`: 8 of 57 windows completed before a
configured `daily_call_cap = 30` stopped it, and every completed window abstained (6 buy
phase, 2 unreadable). Zero findings. A frame from the middle of the reviewed span shows a
running round timer at 1:39, an equipped Classic and the spike pickup prompt: unambiguous
live play that the model called buy phase. This is the same failure as the first run, now
with visual proof, and it is the reason to fix scene recognition before anything else.

Two safeguards were added in response, neither of which fixes the underlying misread:

- The review now diagnoses itself. When abstentions dominate, the report says so in plain
  words, counts the reasons, and points at `scenes validate` instead of silently looking
  like a review of flawless play. The desktop shows it above the fold.
- Reports carry `partial` and `stopped_reason`, so a review truncated by a cap or a
  failure is obvious rather than indistinguishable from a complete one.

## Deterministic HUD reading (implemented 2026-09-21)

The model cannot be argued out of a misread, so the round clock is now read without it.
ffmpeg crops the timer region to a small grayscale raster; the glyphs are segmented and
matched against templates learned from the player's own footage, because Valorant's timer
font is not a system font. No imaging dependency was added.

One inference is drawn from the clock, and only one, because it is the only one the clock
supports on its own: **a clock above 45 seconds means the round is live and the spike is
not down**, since neither the buy phase nor the post-plant spike timer ever shows more.
That is exactly the claim a misread turns into a skipped window, so a long clock overrides
a model phase of `pre_round`, `post_plant`, `retake` or unreadable. `spectating` is never
overridden: the clock on screen belongs to whoever is being watched.

Verified against a generated video with a known clock: without the veto the review skipped
every window as buy phase and produced nothing; with it, the same model on the same footage
produced findings and the report recorded the override. That is the failure from the runs
above, fixed.

Calibrate it once per setup:

```
round-review hud crop <clip> --at 45          # check the box is on the timer
round-review hud learn <clip> --at 45 --reads 1:39   # repeat until no digits are missing
round-review hud read <clip> --at 45          # confirm, and see what it proves
round-review scenes validate scene-labels.json       # scores the model and the HUD side by side
```

`scenes validate` now reports accuracy with and without the clock and counts corrections,
including any that made the answer worse, so the veto itself is measurable rather than
assumed.

## Third Windows run (2026-09-21): cost, and where it goes

A 12:29 Swiftplay reviewed at 91% coverage, 57 windows, `qwen3-vl:4b`: two hours, zero
findings, 56 of 57 windows skipped (47 buy phase, 9 unreadable). The visible frame at 0:01
genuinely was the buy phase, so some of those skips were right; 47 of 57 was not.

The two hours were almost entirely spent deciding **not** to coach. Only one window reached
the coach pass, so ~57 of the ~58 model calls were scene reads, and each carried twelve
1280px frames. Image tokens, not coaching, were the cost.

Three changes came out of it:

- The scene pass now sends `situation_frames` frames (3 by default) spread across the
  window instead of all twelve. Measured at 3 images per call against 12 before.
- Requests set `keep_alive`, so a run of dozens of back-to-back calls does not risk the
  model being unloaded and reloaded between them.
- A review that skips `abstain_streak_limit` windows in a row (20 by default) stops and
  points at `scenes validate`. Establishing that a model cannot read the screen should cost
  minutes, not hours.

Reviews are also timed into the ledger now, so the clip card carries a plain estimate from
what past reviews on that machine actually took.

The deeper point stands: full coverage of a real match pays for a great deal of buy phase,
walking and spectating. Encounter-anchored windows remain the right answer.

## How the report is shaped, and why (2026-09-21)

The output is now modelled on how coaches actually give feedback rather than on what the
model happens to emit. The design decisions and their sources:

- **At most three focus items, fewer preferred.** The best-supported number in the
  literature. Medical-education guidance says two or three per session; esports practice
  guidance says one to three goals and warns against ten; Valorant review checklists say one
  or two mistakes per session. Carpentier & Mageau found corrective-feedback *volume*
  independently predicted lower competence satisfaction, so more items is worse even when
  every item is correct.
- **Corrections first, praise after.** Henley & DiGennaro Reed tested feedback order
  directly: corrective-positive-positive performed best, positive-positive-corrective worst.
  The feedback sandwich is contested rather than debunked, but praise-first is the one
  arrangement with no support, and Ivey research (2025) found recipients detect buffer
  praise and discount both halves.
- **No praise ratio.** The famous 5:1 descends from Fredrickson & Losada, whose model was
  mathematically invalidated and formally withdrawn. Strengths are included when they are
  genuine and specific, zero to three of them, never padded.
- **Praise describes behaviour, never ability.** Mueller & Dweck: ability praise produced
  challenge avoidance and worse resilience. The parser drops praise shorter than 25
  characters or matching a filler phrase, because generic praise is the automated-feedback
  failure mode users dismiss.
- **Merge symptoms into habits.** Findings sharing a checklist id become one habit with all
  its timestamps. Six symptoms of one habit make a report look thorough and the player look
  hopeless.
- **Rank by recurrence and cost.** `coaching/session.py` scores each habit as
  `3*recurrence + 3*cost + 2*control + upstream`, following the factors and weights in the
  research. Recurrence uses the published 0-3 scale. Cost, control and upstream come from a
  per-category table, standing in for the per-round outcome data that sampled frames cannot
  give; every report says so.
- **Grade the decision, not the result.** Baron & Hershey's outcome bias is robust even in
  people who say they are ignoring outcomes. Findings that lean on later-revealed
  information are reported in their own section rather than counted against the player.
- **One practice item, with a success check.** Locke & Latham on specific attainable goals;
  Ericsson's criteria via Bubna et al. require it be solo-repeatable with its own feedback
  signal. Category rules and drills live in `knowledge/drills.json`, paired because published
  rank training plans pair one drill with one in-game rule.
- **State what could not be seen.** No product in the market publishes an observability
  note. For a frame-sampling reviewer it is the difference between trustworthy and merely
  confident, so every report names the sampled coverage, the absence of round outcomes, and
  that it cannot read comms or intent.

Sources: Kluger & DeNisi (1996) feedback intervention theory; Hattie & Timperley feedback
levels; Carpentier & Mageau (2013, 2016) autonomy-supportive feedback; Henley & DiGennaro
Reed (2015) feedback order; Brown, Sokal & Friedman and Nickerson (2018) on positivity
ratios; Mueller & Dweck (1998); Baron & Hershey (1988) and its 2023 replication; Kalyuga et
al. on expertise reversal; Locke & Latham; Bubna et al. (2023) esports deliberate practice;
Otte et al. (2020) on overcoaching. Product conventions from UpForge, Omnic Forge, Leetify,
Refrag Coach, Scope.gg, Mobalytics, Metafy's coach handbook and BetterGamer.

Not built yet: **cross-match habit tracking.** Every competitor is weakest here and it is
the one thing a per-match human review structurally cannot give. Tagging a habit NEW /
REPEAT / IMPROVING / PERSISTENT needs history the ledger could carry, and it would make the
"recurring" section far stronger than within-match repetition alone.

## Fourth Windows run (2026-09-22): what was wrong with the output

A 5:10 clip, 21 windows, 22 findings, `qwen3-vl:8b`. Three real faults:

- The review warned it had "little or nothing to say" while carrying 22 findings, because
  the diagnosis only looked at the share of skipped windows. It now also requires the coached
  windows to have produced little, so a match that is genuinely mostly buy phase and
  spectating reads as normal rather than broken.
- Positioning and trading findings were raised about a player crossing the map alone: told
  to stay in trade range of teammates half a map away. Trade-distance findings are now
  dropped when no enemy was on screen, and the prompt says that moving with nobody visible is
  rotating, not holding an angle.
- Clicking a finding rendered it below the coaching summary, so reading one meant scrolling
  past everything. The review view is now a video and a tabbed sidebar.

Also added: the model may mark what it means with boxes, points and arrows on the frame, and
a finished review records the agent and map so the library is browsable.

## Next implementation priorities

1. Build the labelled validation set with `scenes scaffold` and run `scenes validate`
   against `qwen3-vl:8b` and `qwen3-vl:4b`, at one and three frames per case. Treat the
   accuracy numbers as the gate for everything below; do not assume either change
   improves accuracy.
2. Extend deterministic reading past the clock if the clock alone proves insufficient: the
   spike-planted indicator and the alive-player rows sit at fixed positions too, and would
   separate post-plant from retake without the model.
3. Replace evenly spaced selection with encounter timestamps. Prefer recorded
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
