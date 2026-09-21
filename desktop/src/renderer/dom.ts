import type { Clip, Finding, Knowledge, PlayerContext } from '../shared/types';
import { formatClock, markerPercent, type Marker } from './timeline';

export interface Progress {
  windows_done: number;
  windows_total: number;
}

export interface ClipHandlers {
  onAnalyze: (key: string) => void;
  onOpen: (key: string) => void;
}

const ANALYZABLE = new Set<string>(['new', 'failed', 'skipped']);

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

const statusLabel = (clip: Clip, progress: Progress | undefined): string => {
  if (clip.status === 'running' && progress && progress.windows_total > 0) {
    return `analyzing ${progress.windows_done} / ${progress.windows_total}`;
  }
  return clip.status;
};

export const renderClipList = (
  root: HTMLElement,
  clips: Clip[],
  progressByJob: Record<string, Progress>,
  handlers: ClipHandlers,
): void => {
  root.replaceChildren();
  if (clips.length === 0) {
    root.append(el('p', 'empty', 'No recordings found in the configured recordings folder.'));
    return;
  }
  for (const clip of clips) {
    const row = el('div', `clip status-${clip.status}`);
    row.dataset['clip'] = clip.key;
    row.append(el('span', 'name', clip.name));
    const progress = clip.job_id ? progressByJob[clip.job_id] : undefined;
    row.append(el('span', 'status', statusLabel(clip, progress)));
    if (clip.error) row.append(el('span', 'error', clip.error));
    if (ANALYZABLE.has(clip.status)) {
      const button = el('button', undefined, clip.status === 'new' ? 'Analyze' : 'Retry');
      button.dataset['action'] = 'analyze';
      button.addEventListener('click', () => handlers.onAnalyze(clip.key));
      row.append(button);
    }
    if (clip.status === 'done') {
      const button = el('button', undefined, 'Open review');
      button.dataset['action'] = 'open';
      button.addEventListener('click', () => handlers.onOpen(clip.key));
      row.append(button);
    }
    root.append(row);
  }
};

export const renderMarkers = (
  track: HTMLElement,
  markers: Marker[],
  durationS: number,
  selectedId: string | null,
  onSelect: (id: string) => void,
): void => {
  track.replaceChildren();
  for (const marker of markers) {
    const node = el('button', `marker cat-${marker.finding.category}`);
    node.dataset['marker'] = marker.id;
    node.title = `${formatClock(marker.timestamp_s)} ${marker.finding.category}`;
    node.style.left = `${markerPercent(marker.timestamp_s, durationS)}%`;
    if (marker.id === selectedId) node.classList.add('selected');
    node.addEventListener('click', () => onSelect(marker.id));
    track.append(node);
  }
};

const section = (label: string, body: string): HTMLElement => {
  const wrap = el('div', 'section');
  wrap.append(el('strong', undefined, `${label}: `), document.createTextNode(body));
  return wrap;
};

export const renderFindingCard = (
  card: HTMLElement,
  finding: Finding | null,
  frameUrl: string | null,
): void => {
  card.replaceChildren();
  if (finding === null) {
    card.append(
      el(
        'p',
        'placeholder',
        'Click a marker under the video to see what could have gone differently.',
      ),
    );
    return;
  }
  card.append(el('h3', undefined, `${formatClock(finding.timestamp_s)} · ${finding.category}`));
  if (finding.check_label) card.append(el('p', 'check', finding.check_label));
  card.append(el('p', 'observation', finding.observation));
  if (frameUrl) {
    const img = el('img', 'evidence');
    img.src = frameUrl;
    img.alt = `Evidence frame at ${formatClock(finding.timestamp_s)}`;
    card.append(img);
  }
  card.append(section('What you could see', finding.visible_evidence));
  card.append(section('What you knew', finding.information_available_to_player));
  if (finding.information_revealed_later.trim()) {
    card.append(section("What you couldn't have known", finding.information_revealed_later));
  }
  if (finding.assumption_flags.length > 0) {
    card.append(section('Assumptions', finding.assumption_flags.join(', ')));
  }
  card.append(section('Try instead', finding.suggested_alternative));
  card.append(el('p', 'confidence', `Confidence: ${Math.round(finding.confidence * 100)}%`));
};

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
