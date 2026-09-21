/**
 * @jest-environment jsdom
 */
import {
  renderClipList,
  renderContextBar,
  renderFindingCard,
  renderFindingList,
  renderReviewCoverage,
  renderMarkers,
} from '../../src/renderer/dom';
import { EMPTY_CONTEXT } from '../../src/shared/types';
import { collectMarkers } from '../../src/renderer/timeline';
import { clip, finding, report } from './fixtures';

describe('renderClipList', () => {
  it('renders one row per clip with status and an Analyze button for analyzable clips', () => {
    const root = document.createElement('div');
    const clicked: string[] = [];
    renderClipList(
      root,
      [
        clip(),
        clip({ name: 'b.mp4', key: 'k2', status: 'running', job_id: 'j' }),
        clip({ name: 'c.mp4', key: 'k3', status: 'done' }),
      ],
      { j: { windows_done: 1, windows_total: 3 } },
      { onAnalyze: key => clicked.push(`a:${key}`), onOpen: key => clicked.push(`o:${key}`) },
    );
    const rows = root.querySelectorAll('[data-clip]');
    expect(rows).toHaveLength(3);
    expect(rows[0]?.textContent).toContain('match.mp4');
    expect(rows[1]?.textContent).toContain('1 / 3');
    (rows[0]?.querySelector('button[data-action="analyze"]') as HTMLButtonElement).click();
    (rows[2]?.querySelector('button[data-action="open"]') as HTMLButtonElement).click();
    expect(rows[1]?.querySelector('button[data-action="analyze"]')).toBeNull();
    expect(clicked).toEqual(['a:0123456789abcdef', 'o:k3']);
  });

  it('shows an empty-state message', () => {
    const root = document.createElement('div');
    renderClipList(root, [], {}, { onAnalyze: () => undefined, onOpen: () => undefined });
    expect(root.textContent).toContain('No recordings found');
  });
});

describe('renderMarkers', () => {
  it('positions a marker per finding and reports clicks', () => {
    const track = document.createElement('div');
    const selected: string[] = [];
    renderMarkers(track, collectMarkers(report()), 600, 'w1-f0', id => selected.push(id));
    const marks = track.querySelectorAll('[data-marker]');
    expect(marks).toHaveLength(3);
    expect((marks[1] as HTMLElement).style.left).toBe('50.83333333333333%');
    expect(marks[1]?.classList.contains('selected')).toBe(true);
    (marks[2] as HTMLElement).click();
    expect(selected).toEqual(['w1-f1']);
  });
});

describe('renderFindingCard', () => {
  it('renders every anti-hindsight section and the evidence image', () => {
    const card = document.createElement('div');
    renderFindingCard(
      card,
      finding({ information_revealed_later: 'Enemy behind box.' }),
      'http://x/frames/w00_004.jpg',
    );
    const text = card.textContent ?? '';
    expect(text).toContain('1:04');
    expect(text).toContain('positioning');
    expect(text).toContain('Crosshair placement / Crosshair is at head height');
    expect(text).toContain('Held a wide angle.');
    expect(text).toContain('What you could see');
    expect(text).toContain("What you couldn't have known");
    expect(text).toContain('Enemy behind box.');
    expect(text).toContain('enemy position');
    expect(text).toContain('Tighten the angle.');
    expect(text).toContain('80%');
    expect(card.querySelector('img')?.getAttribute('src')).toBe('http://x/frames/w00_004.jpg');
  });
  it('renders a placeholder when no finding is selected', () => {
    const card = document.createElement('div');
    renderFindingCard(card, null, null);
    expect(card.textContent).toContain('Click a marker');
  });
});

describe('review navigation', () => {
  it('makes findings at the same timestamp individually selectable', () => {
    const root = document.createElement('div');
    const markers = collectMarkers(report()).map(m => ({ ...m, timestamp_s: 30 }));
    const selected: string[] = [];
    renderFindingList(root, markers, markers[1]?.id ?? null, id => selected.push(id));
    const buttons = root.querySelectorAll('button');
    expect(buttons).toHaveLength(3);
    expect(buttons[1]?.getAttribute('aria-pressed')).toBe('true');
    buttons[2]?.click();
    expect(selected).toEqual([markers[2]?.id]);
  });

  it('shows actual sampled coverage and validation warnings', () => {
    const root = document.createElement('div');
    renderReviewCoverage(root, report());
    expect(root.textContent).toContain('24s of 600s (4%)');
    expect(root.textContent).toContain('not a full-video review');
    expect(root.textContent).toContain('window warning');
  });
});

describe('renderContextBar', () => {
  it('renders rank, agent, map and side controls from knowledge and reports changes', () => {
    const bar = document.createElement('div');
    const changes: Array<[string, string]> = [];
    renderContextBar(
      bar,
      { ...EMPTY_CONTEXT, rank: 'Gold', agent: 'Jett' },
      {
        agents: [
          { id: 'jett', name: 'Jett', role: 'duelist' },
          { id: 'sova', name: 'Sova', role: 'initiator' },
        ],
        maps: [{ id: 'ascent', name: 'Ascent' }],
        ranks: ['Silver', 'Gold'],
        checklist: [],
      },
      (field, value) => changes.push([field, value]),
    );
    const rank = bar.querySelector('select[data-field="rank"]') as HTMLSelectElement;
    const agent = bar.querySelector('select[data-field="agent"]') as HTMLSelectElement;
    const map = bar.querySelector('select[data-field="map"]') as HTMLSelectElement;
    const side = bar.querySelector('select[data-field="side"]') as HTMLSelectElement;
    const focus = bar.querySelector('input[data-field="focus"]') as HTMLInputElement;
    expect(rank.value).toBe('Gold');
    expect(agent.value).toBe('Jett');
    expect(map.value).toBe('');
    expect(Array.from(agent.options).map(o => o.value)).toEqual(['', 'Jett', 'Sova']);
    expect(Array.from(side.options).map(o => o.value)).toEqual(['', 'attack', 'defense']);
    map.value = 'Ascent';
    map.dispatchEvent(new Event('change'));
    focus.value = 'entries';
    focus.dispatchEvent(new Event('change'));
    expect(changes).toEqual([
      ['map', 'Ascent'],
      ['focus', 'entries'],
    ]);
  });
});
