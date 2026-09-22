import type {
  Answer,
  Clip,
  Habit,
  Knowledge,
  PlayerContext,
  Report,
  ReportSummary,
  ReviewOptions,
  Settings,
  StrengthItem,
} from '../shared/types';
import {
  formatBytes,
  formatDuration,
  formatRelativeTime,
  isLongReview,
  reviewSummary,
} from './format';
import { SUGGESTED_QUESTIONS, type AskState } from './state';
import { formatClock, rulerTicks, type CoverageBand, type Marker } from './timeline';

const el = <K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const button = (
  label: string,
  action: string,
  onClick: () => void,
  className = 'btn',
): HTMLButtonElement => {
  const node = el('button', className, label);
  node.dataset['action'] = action;
  node.addEventListener('click', onClick);
  return node;
};

// ---------------------------------------------------------------------------- clip library

export interface Progress {
  windows_done: number;
  windows_total: number;
}

export interface ClipHandlers {
  onAnalyze: (key: string) => void;
  onReanalyze: (key: string) => void;
  onOpen: (key: string) => void;
}

export interface ClipListProps {
  clips: Clip[];
  jobs: Record<string, Progress>;
  options: ReviewOptions;
  settings: Settings | null;
  now: number;
  handlers: ClipHandlers;
}

const NEVER_REVIEWED = new Set<string>(['new', 'skipped']);
const OPENABLE = new Set<string>(['done', 'partial']);
const BUSY = new Set<string>(['queued', 'running']);

const statusPill = (clip: Clip, progress: Progress | undefined): HTMLElement => {
  const pill = el('span', `pill pill-${clip.status}`);
  if (clip.status === 'running' && progress && progress.windows_total > 0) {
    pill.textContent = `analyzing ${progress.windows_done} / ${progress.windows_total}`;
  } else if (clip.status === 'running') {
    pill.textContent = 'starting';
  } else {
    pill.textContent = clip.status;
  }
  return pill;
};

const progressBar = (progress: Progress): HTMLElement => {
  const track = el('div', 'progress');
  const fill = el('div', 'progress-fill');
  fill.dataset['progress'] = 'true';
  const pct =
    progress.windows_total > 0 ? (100 * progress.windows_done) / progress.windows_total : 0;
  fill.style.width = `${pct}%`;
  track.append(fill);
  return track;
};

export const renderClipList = (root: HTMLElement, props: ClipListProps): void => {
  root.replaceChildren();
  if (props.clips.length === 0) {
    root.append(
      el(
        'p',
        'empty',
        'No recordings in the configured folder. Record a match and it appears here.',
      ),
    );
    return;
  }
  for (const clip of props.clips) {
    const card = el('article', `card status-${clip.status}`);
    card.dataset['clip'] = clip.key;

    const head = el('div', 'card-head');
    head.append(el('h3', 'card-title', clip.name));
    const progress = clip.job_id ? props.jobs[clip.job_id] : undefined;
    head.append(statusPill(clip, progress));
    card.append(head);

    const meta = [
      formatDuration(clip.duration_s),
      formatBytes(clip.size_bytes),
      formatRelativeTime(clip.mtime, props.now),
    ].join('  ·  ');
    card.append(el('p', 'card-meta', meta));

    if (NEVER_REVIEWED.has(clip.status) && props.settings) {
      const plan = el('p', 'card-plan', reviewSummary(clip, props.options, props.settings));
      if (isLongReview(clip, props.options)) plan.classList.add('long');
      card.append(plan);
    }
    if (progress && BUSY.has(clip.status)) card.append(progressBar(progress));
    if (clip.error) card.append(el('p', 'card-error', clip.error));

    const actions = el('div', 'card-actions');
    if (NEVER_REVIEWED.has(clip.status)) {
      actions.append(button('Analyze', 'analyze', () => props.handlers.onAnalyze(clip.key)));
    }
    if (OPENABLE.has(clip.status)) {
      actions.append(button('Open review', 'open', () => props.handlers.onOpen(clip.key)));
    }
    if (OPENABLE.has(clip.status) || clip.status === 'failed') {
      actions.append(
        button('Re-analyze', 'reanalyze', () => props.handlers.onReanalyze(clip.key), 'btn ghost'),
      );
    }
    if (actions.childElementCount > 0) card.append(actions);
    root.append(card);
  }
};

/**
 * The deterministic HUD clock check silently does nothing until the digits are learned, so
 * say so where the review options are chosen rather than letting it look like it works.
 */
export const renderHudHint = (root: HTMLElement, settings: Settings | null): void => {
  root.replaceChildren();
  const show = settings !== null && settings.hud_check && !settings.hud_ready;
  root.hidden = !show;
  if (!show || settings === null) return;
  root.append(
    el(
      'p',
      'hint',
      `Timer check is on but untrained, so it is doing nothing. Teach it once with ` +
        `\`round-review hud learn\`; still missing: ${settings.hud_missing_characters.join(' ')}.`,
    ),
  );
};

// ------------------------------------------------------------------------- review presets

const PRESET_LABELS: ReadonlyArray<[string, string, string]> = [
  ['full', 'Whole clip', 'Review every part of the recording'],
  ['first_minute', 'First minute', 'Review only the first minute of gameplay'],
  ['sampled', 'Quick samples', 'A few spread-out windows, fastest'],
];

export const renderPresetPicker = (
  root: HTMLElement,
  active: string,
  onChoose: (preset: string) => void,
): void => {
  root.replaceChildren();
  for (const [preset, label, title] of PRESET_LABELS) {
    const node = el('button', 'seg', label);
    node.dataset['preset'] = preset;
    node.title = title;
    node.setAttribute('aria-pressed', String(preset === active));
    node.addEventListener('click', () => onChoose(preset));
    root.append(node);
  }
};

// ------------------------------------------------------------------------------- timeline

export interface TimelineProps {
  markers: Marker[];
  coverage: CoverageBand[];
  durationS: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onSeek: (timestampS: number) => void;
}

/**
 * The strip under the video: shaded bands for the stretches that were reviewed, a numbered
 * pin per finding, and a time ruler. Clicking anywhere scrubs; clicking a pin opens it.
 */
export const renderTimeline = (root: HTMLElement, props: TimelineProps): void => {
  root.replaceChildren();
  const track = el('div', 'tl-track');

  for (const band of props.coverage) {
    const node = el('div', 'tl-band');
    node.dataset['band'] = String(band.index);
    node.style.left = `${band.leftPercent}%`;
    node.style.width = `${band.widthPercent}%`;
    node.title = `Reviewed ${formatClock((band.leftPercent / 100) * props.durationS)} onward, ${band.findings} finding(s)`;
    track.append(node);
  }

  track.addEventListener('click', event => {
    const bounds = track.getBoundingClientRect();
    if (bounds.width > 0) {
      const ratio = (event.clientX - bounds.left) / bounds.width;
      props.onSeek(Math.min(1, Math.max(0, ratio)) * props.durationS);
    }
  });

  for (const marker of props.markers) {
    const pin = el('button', `tl-pin cat-${marker.finding.category}`, String(marker.ordinal));
    pin.dataset['marker'] = marker.id;
    pin.style.left = `${(marker.timestamp_s / Math.max(props.durationS, 1)) * 100}%`;
    pin.title = `${formatClock(marker.timestamp_s)} · ${marker.finding.category}`;
    if (marker.id === props.selectedId) pin.classList.add('selected');
    pin.addEventListener('click', event => {
      event.stopPropagation();
      props.onSelect(marker.id);
    });
    track.append(pin);
  }
  root.append(track);

  const ruler = el('div', 'tl-ruler');
  for (const tick of rulerTicks(props.durationS)) {
    const node = el('span', 'tl-tick', tick.label);
    node.dataset['tick'] = String(tick.timestamp_s);
    node.style.left = `${tick.leftPercent}%`;
    ruler.append(node);
  }
  root.append(ruler);
};

// ------------------------------------------------------------------------------- coverage

/** The pipeline's own diagnosis of an empty review, if it made one. */
const DIAGNOSIS_MARKER = 'misreading the screen';

export const diagnosisOf = (report: Report): string | null =>
  report.warnings.find(w => w.includes(DIAGNOSIS_MARKER)) ?? null;

export const renderCoverageSummary = (root: HTMLElement, report: Report): void => {
  root.replaceChildren();

  // A partial or misread review is the first thing to know, not a note to go digging for.
  if (report.partial && report.stopped_reason) {
    const banner = el('div', 'notice partial');
    banner.dataset['partial'] = 'true';
    banner.append(el('strong', undefined, 'Partial review. '));
    banner.append(document.createTextNode(report.stopped_reason));
    root.append(banner);
  }
  const diagnosis = diagnosisOf(report);
  if (diagnosis) {
    const banner = el('div', 'notice diagnosis', diagnosis);
    banner.dataset['diagnosis'] = 'true';
    root.append(banner);
  }

  const reviewed = report.windows.reduce((total, w) => total + (w.end_s - w.start_s), 0);
  const duration = report.recording.duration_s;
  const percent = duration > 0 ? Math.round((100 * reviewed) / duration) : 0;
  const windows = report.windows.length;
  root.append(
    el(
      'p',
      'coverage',
      percent >= 99
        ? `Whole clip reviewed · ${windows} windows · ${report.model}`
        : `Reviewed ${formatDuration(reviewed)} of ${formatDuration(duration)} (${percent}%) · ${windows} windows · ${report.model}`,
    ),
  );
  const notes = [...report.warnings, ...report.windows.flatMap(w => w.warnings)].filter(
    note => note !== diagnosis,
  );
  if (notes.length > 0) {
    const details = el('details', 'notes');
    details.append(el('summary', undefined, `Review notes (${notes.length})`));
    for (const note of notes) details.append(el('p', 'note', note));
    root.append(details);
  }
};

// -------------------------------------------------------------------------- finding list

export const renderFindingList = (
  root: HTMLElement,
  markers: Marker[],
  selectedId: string | null,
  onSelect: (id: string) => void,
  emptyReason?: string | null,
): void => {
  root.replaceChildren();
  if (markers.length === 0) {
    root.append(
      el(
        'p',
        'empty',
        emptyReason ?? 'No findings in the reviewed windows. Check the review notes for why.',
      ),
    );
    return;
  }
  let lastCategory: string | null = null;
  for (const marker of markers) {
    if (marker.finding.category !== lastCategory) {
      lastCategory = marker.finding.category;
      const count = markers.filter(m => m.finding.category === lastCategory).length;
      const heading = el('h5', 'finding-group');
      heading.dataset['category'] = lastCategory;
      heading.textContent = `${lastCategory} · ${count}`;
      root.append(heading);
    }
    const row = el('button', 'finding-row');
    row.dataset['finding'] = marker.id;
    row.setAttribute('aria-pressed', String(marker.id === selectedId));
    if (marker.id === selectedId) row.classList.add('selected');
    row.append(el('span', 'finding-index', String(marker.ordinal)));
    const body = el('span', 'finding-body');
    const head = el('span', 'finding-head');
    head.append(el('span', 'finding-time', formatClock(marker.timestamp_s)));
    head.append(el('span', `finding-cat cat-${marker.finding.category}`, marker.finding.category));
    body.append(head);
    body.append(el('span', 'finding-text', marker.finding.observation));
    row.append(body);
    row.addEventListener('click', () => onSelect(marker.id));
    root.append(row);
  }
};

// -------------------------------------------------------------------------- finding card

export interface FindingCardProps {
  marker: Marker | null;
  frameUrl: string | null;
  situationSummary: string | null;
}

const section = (label: string, body: string, className = 'section'): HTMLElement => {
  const wrap = el('div', className);
  wrap.append(el('span', 'section-label', label));
  wrap.append(el('p', 'section-body', body));
  return wrap;
};

export const renderFindingCard = (card: HTMLElement, props: FindingCardProps): void => {
  card.replaceChildren();
  const marker = props.marker;
  if (marker === null) {
    card.append(
      el('p', 'placeholder', 'Select a finding on the timeline to see what to do differently.'),
    );
    return;
  }
  const finding = marker.finding;

  const head = el('div', 'finding-card-head');
  head.append(el('span', 'badge', String(marker.ordinal)));
  head.append(el('h3', undefined, `${formatClock(finding.timestamp_s)} · ${finding.category}`));
  const confidence = el('span', 'confidence', `${Math.round(finding.confidence * 100)}%`);
  confidence.title = 'How confident the model is in this finding';
  head.append(confidence);
  card.append(head);

  if (finding.check_label) card.append(el('p', 'check', finding.check_label));
  card.append(el('p', 'observation', finding.observation));

  // The fix is the point of the whole app, so it sits above the evidence and the reasoning.
  card.append(section('Try instead', finding.suggested_alternative, 'section fix'));

  if (props.frameUrl) {
    const figure = el('figure', 'evidence');
    const img = el('img');
    img.src = props.frameUrl;
    img.alt = `Frame at ${formatClock(finding.timestamp_s)}`;
    figure.append(img);
    figure.append(el('figcaption', undefined, `Frame at ${formatClock(finding.timestamp_s)}`));
    card.append(figure);
  }

  card.append(section('What you could see', finding.visible_evidence));
  card.append(section('What you knew', finding.information_available_to_player));
  if (finding.information_revealed_later.trim()) {
    card.append(
      section("What you couldn't have known", finding.information_revealed_later, 'section later'),
    );
  }
  if (finding.assumption_flags.length > 0) {
    card.append(section('Assumptions', finding.assumption_flags.join(', ')));
  }
  if (props.situationSummary) card.append(section('Situation', props.situationSummary));
};

// --------------------------------------------------------------------------- context bar

const SIDES: ReadonlyArray<[string, string]> = [
  ['', 'Side: auto'],
  ['attack', 'Attack'],
  ['defense', 'Defense'],
];

const select = (
  field: keyof PlayerContext,
  options: ReadonlyArray<[string, string]>,
  current: string | null,
  onChange: (field: keyof PlayerContext, value: string) => void,
): HTMLSelectElement => {
  const node = el('select');
  node.dataset['field'] = field;
  for (const [value, label] of options) {
    const option = el('option', undefined, label);
    option.value = value;
    node.append(option);
  }
  node.value = current ?? '';
  node.addEventListener('change', () => onChange(field, node.value));
  return node;
};

/** Rank / agent / map / side / focus controls. Blank means "let the model detect it". */
export const renderContextBar = (
  bar: HTMLElement,
  context: PlayerContext,
  knowledge: Knowledge,
  onChange: (field: keyof PlayerContext, value: string) => void,
): void => {
  bar.replaceChildren();
  const blank = (label: string): [string, string] => ['', label];
  bar.append(
    select(
      'rank',
      [blank('Rank'), ...knowledge.ranks.map((r): [string, string] => [r, r])],
      context.rank,
      onChange,
    ),
  );
  bar.append(
    select(
      'agent',
      [blank('Agent: auto'), ...knowledge.agents.map((a): [string, string] => [a.name, a.name])],
      context.agent,
      onChange,
    ),
  );
  bar.append(
    select(
      'map',
      [blank('Map: auto'), ...knowledge.maps.map((m): [string, string] => [m.name, m.name])],
      context.map,
      onChange,
    ),
  );
  bar.append(select('side', SIDES, context.side, onChange));
  const focus = el('input');
  focus.type = 'text';
  focus.placeholder = 'Focus (e.g. entries, post-plant)';
  focus.dataset['field'] = 'focus';
  focus.value = context.focus ?? '';
  focus.addEventListener('change', () => onChange('focus', focus.value));
  bar.append(focus);
};

// ------------------------------------------------------------------------- coach panel

const jumpRow = (timestamps: number[], onSeek: (timestampS: number) => void): HTMLElement => {
  const wrap = el('div', 'jumps');
  wrap.append(el('span', 'section-label', 'Where to look'));
  for (const timestamp of timestamps) {
    const jump = el('button', 'jump', formatClock(timestamp));
    jump.dataset['jump'] = String(timestamp);
    jump.addEventListener('click', event => {
      event.stopPropagation();
      onSeek(timestamp);
    });
    wrap.append(jump);
  }
  return wrap;
};

const focusCard = (
  index: number,
  habit: Habit,
  onSeek: (timestampS: number) => void,
): HTMLElement => {
  const card = el('article', `focus cat-${habit.category}`);
  card.dataset['focus'] = habit.check_id;
  const head = el('div', 'focus-head');
  head.append(el('span', 'badge', String(index)));
  head.append(el('h4', undefined, habit.observation));
  card.append(head);

  const times = habit.count === 1 ? 'once' : `${habit.count} times`;
  card.append(el('p', 'focus-meta', `${habit.category} · ${times}`));
  card.append(jumpRow(habit.timestamps, onSeek));
  const fix = el('div', 'section fix');
  fix.append(el('span', 'section-label', 'Try instead'));
  fix.append(el('p', 'section-body', habit.suggested_alternative));
  card.append(fix);
  if (habit.assumption_flags.length > 0) {
    card.append(
      el('p', 'focus-assumed', `Assumed, not seen: ${habit.assumption_flags.join(', ')}`),
    );
  }
  // The whole card seeks to the clearest instance, so one click gets you there.
  card.addEventListener('click', () => onSeek(habit.timestamps[0] ?? 0));
  return card;
};

const strengthRow = (strength: StrengthItem): HTMLElement => {
  const row = el('div', 'strength');
  row.append(
    el('span', 'strength-time', formatClock(strength.timestamp_s)),
    el('span', 'strength-cat', strength.category),
  );
  row.append(el('p', 'strength-body', strength.observation));
  row.append(el('p', 'strength-why', strength.why_it_worked));
  return row;
};

/**
 * The coach's write-up: verdict, then the corrections, then genuine praise, then the one
 * thing to practise. Corrections come first deliberately; praise placed first reads as a
 * cushion and gets discounted along with the criticism.
 */
export const renderCoachPanel = (
  root: HTMLElement,
  summary: ReportSummary,
  onSeek: (timestampS: number) => void,
): void => {
  root.replaceChildren();

  const verdict = el('div', 'verdict');
  verdict.dataset['verdict'] = 'true';
  verdict.append(el('span', 'section-label', 'The one thing to fix'));
  verdict.append(el('p', 'verdict-body', summary.verdict));
  if (summary.rank_focus) {
    verdict.append(el('p', 'verdict-rank', `At your rank: ${summary.rank_focus}`));
  }
  root.append(verdict);

  if (summary.focus.length > 0) {
    root.append(el('h3', 'coach-heading', 'What to work on'));
    summary.focus.forEach((habit, i) => root.append(focusCard(i + 1, habit, onSeek)));
  }

  if (summary.strengths.length > 0) {
    root.append(el('h3', 'coach-heading', 'What worked'));
    const wrap = el('div', 'strengths');
    for (const strength of summary.strengths) wrap.append(strengthRow(strength));
    root.append(wrap);
  }

  if (summary.hindsight.length > 0) {
    const section = el('div', 'notice hindsight');
    section.dataset['hindsight'] = 'true';
    section.append(el('span', 'section-label', 'Judged with hindsight, so treat with care'));
    for (const habit of summary.hindsight) {
      section.append(
        el(
          'p',
          'section-body',
          `${formatClock(habit.timestamps[0] ?? 0)} — ${habit.observation} Only clear afterwards: ${habit.information_revealed_later}`,
        ),
      );
    }
    root.append(section);
  }

  if (summary.also_seen.length > 0) {
    const section = el('details', 'also-seen');
    section.dataset['alsoSeen'] = 'true';
    section.setAttribute('data-also-seen', 'true');
    section.append(el('summary', undefined, `Also seen (${summary.also_seen.length})`));
    for (const item of summary.also_seen) {
      const times = item.count === 1 ? 'once' : `${item.count} times`;
      section.append(
        el('p', 'note', `${item.category}: ${item.check_label ?? item.check_id} (${times})`),
      );
    }
    root.append(section);
  }

  if (summary.practice) {
    const practice = el('div', 'practice');
    practice.dataset['practice'] = 'true';
    practice.append(el('h3', 'coach-heading', 'Before your next game'));
    practice.append(el('span', 'section-label', 'In-game rule'));
    practice.append(el('p', 'section-body', summary.practice.rule));
    practice.append(el('span', 'section-label', 'Drill'));
    practice.append(el('p', 'section-body', summary.practice.drill));
    practice.append(el('span', 'section-label', 'How you will know it worked'));
    practice.append(el('p', 'section-body', summary.practice.success_check));
    root.append(practice);
  }
};

// --------------------------------------------------------------------------- ask the coach

export interface AskHandlers {
  onAsk: (question: string) => void;
  onRangeChange: (startS: number, endS: number) => void;
}

const numberField = (
  field: string,
  value: number,
  onChange: (value: number) => void,
): HTMLInputElement => {
  const input = el('input', 'ask-time');
  input.type = 'number';
  input.min = '0';
  input.step = '1';
  input.dataset['field'] = field;
  input.value = String(Math.round(value));
  input.addEventListener('change', () => onChange(Number(input.value)));
  return input;
};

/**
 * Ask about one stretch of the recording. The range is prefilled from wherever the video is,
 * so the common case is: pause on the moment, pick a suggestion, send.
 */
export const renderAskBox = (root: HTMLElement, ask: AskState, handlers: AskHandlers): void => {
  root.replaceChildren();
  root.append(el('h3', 'coach-heading', 'Ask about a moment'));

  const range = el('div', 'ask-range');
  range.append(el('span', 'ask-label', 'From'));
  range.append(
    numberField('start', ask.start_s, value => handlers.onRangeChange(value, ask.end_s)),
  );
  range.append(el('span', 'ask-label', 'to'));
  range.append(numberField('end', ask.end_s, value => handlers.onRangeChange(ask.start_s, value)));
  range.append(
    el('span', 'ask-clock', `seconds  (${formatClock(ask.start_s)} to ${formatClock(ask.end_s)})`),
  );
  root.append(range);

  if (ask.end_s - ask.start_s > ask.maxSpanS) {
    const warning = el(
      'p',
      'ask-warning',
      `That is longer than one question covers, so only the first ${formatClock(ask.maxSpanS)} will be looked at.`,
    );
    warning.dataset['rangeWarning'] = 'true';
    warning.setAttribute('data-range-warning', 'true');
    root.append(warning);
  }

  const input = el('textarea', 'ask-input');
  input.dataset['field'] = 'question';
  input.rows = 2;
  input.placeholder = 'What should I have done instead?';
  input.disabled = ask.pending;
  root.append(input);

  const chips = el('div', 'ask-suggestions');
  for (const suggestion of SUGGESTED_QUESTIONS) {
    const chip = el('button', 'chip', suggestion);
    chip.dataset['suggestion'] = suggestion;
    chip.addEventListener('click', () => {
      input.value = suggestion;
      input.focus();
    });
    chips.append(chip);
  }
  root.append(chips);

  const actions = el('div', 'ask-actions');
  const send = el('button', 'btn', ask.pending ? 'Thinking…' : 'Ask');
  send.dataset['action'] = 'ask';
  send.disabled = ask.pending;
  send.addEventListener('click', () => {
    const question = input.value.trim();
    if (question) handlers.onAsk(question);
  });
  actions.append(send);
  if (ask.pending) {
    actions.append(
      el(
        'span',
        'ask-pending',
        'Thinking about that moment. This takes about as long as one window of a review.',
      ),
    );
  }
  root.append(actions);
};

const answerCard = (answer: Answer, onSeek: (timestampS: number) => void): HTMLElement => {
  const card = el('article', answer.answerable ? 'answer' : 'answer unanswerable');
  if (!answer.answerable) card.dataset['unanswerable'] = 'true';

  const head = el('div', 'answer-head');
  const question = el('h4', 'answer-question', answer.question);
  question.dataset['answerQuestion'] = 'true';
  head.append(question);
  const jump = el('button', 'jump', formatClock(answer.start_s));
  jump.dataset['answerJump'] = 'true';
  jump.title = `Back to ${formatClock(answer.start_s)}–${formatClock(answer.end_s)}`;
  jump.addEventListener('click', () => onSeek(answer.start_s));
  head.append(jump);
  card.append(head);

  card.append(el('p', 'answer-body', answer.answer));

  for (const alternative of answer.alternatives) {
    const option = el('div', 'alternative');
    option.append(el('p', 'alternative-action', alternative.action));
    option.append(el('p', 'alternative-why', alternative.why));
    card.append(option);
  }

  if (answer.what_you_could_see) {
    card.append(section('What you could see', answer.what_you_could_see));
  }
  if (answer.what_you_could_not_know.trim()) {
    card.append(
      section("What you couldn't have known", answer.what_you_could_not_know, 'section later'),
    );
  }
  if (answer.assumptions.length > 0) {
    card.append(el('p', 'answer-assumed', `Assumed, not seen: ${answer.assumptions.join(', ')}`));
  }
  return card;
};

/** Newest first, so the answer you just waited for is at the top. */
export const renderAnswers = (
  root: HTMLElement,
  answers: Answer[],
  onSeek: (timestampS: number) => void,
): void => {
  root.replaceChildren();
  for (const answer of [...answers].reverse()) root.append(answerCard(answer, onSeek));
};
