/**
 * @jest-environment jsdom
 */
import {
  renderClipList,
  renderContextBar,
  renderCoverageSummary,
  renderFindingCard,
  renderFindingList,
  renderPresetPicker,
  renderTimeline,
} from '../../src/renderer/dom';
import { collectMarkers, coverageBands } from '../../src/renderer/timeline';
import { EMPTY_CONTEXT } from '../../src/shared/types';
import { clip, finding, report, settings } from './fixtures';

const NOW = Date.UTC(2026, 8, 21, 12, 0, 0) / 1000;
const noopHandlers = {
  onAnalyze: () => undefined,
  onReanalyze: () => undefined,
  onOpen: () => undefined,
};

describe('renderClipList', () => {
  const options = { coverage: 'full' as const, max_span_s: null, max_windows: null };

  it('renders a card per clip with duration, size and what a review will do', () => {
    const root = document.createElement('div');
    renderClipList(root, {
      clips: [clip()],
      jobs: {},
      options,
      settings: settings(),
      now: NOW,
      handlers: noopHandlers,
    });
    const card = root.querySelector('[data-clip]')!;
    expect(card.textContent).toContain('match.mp4');
    expect(card.textContent).toContain('10:00');
    expect(card.textContent).toContain('47 windows');
  });

  it('offers Analyze on a new clip and Re-analyze plus Open on a finished one', () => {
    const root = document.createElement('div');
    const clicked: string[] = [];
    renderClipList(root, {
      clips: [clip(), clip({ name: 'b.mp4', key: 'k2', status: 'done' })],
      jobs: {},
      options,
      settings: settings(),
      now: NOW,
      handlers: {
        onAnalyze: key => clicked.push(`a:${key}`),
        onReanalyze: key => clicked.push(`r:${key}`),
        onOpen: key => clicked.push(`o:${key}`),
      },
    });
    const [first, second] = Array.from(root.querySelectorAll('[data-clip]'));
    expect(first!.querySelector('[data-action="analyze"]')).not.toBeNull();
    expect(first!.querySelector('[data-action="open"]')).toBeNull();
    (second!.querySelector('[data-action="reanalyze"]') as HTMLButtonElement).click();
    (second!.querySelector('[data-action="open"]') as HTMLButtonElement).click();
    (first!.querySelector('[data-action="analyze"]') as HTMLButtonElement).click();
    expect(clicked).toEqual(['r:k2', 'o:k2', 'a:0123456789abcdef']);
  });

  it('shows a progress bar with window counts while running', () => {
    const root = document.createElement('div');
    renderClipList(root, {
      clips: [clip({ status: 'running', job_id: 'j' })],
      jobs: { j: { windows_done: 12, windows_total: 47 } },
      options,
      settings: settings(),
      now: NOW,
      handlers: noopHandlers,
    });
    const card = root.querySelector('[data-clip]')!;
    expect(card.textContent).toContain('12 / 47');
    const bar = card.querySelector('[data-progress]') as HTMLElement;
    expect(bar.style.width).toBe(`${(100 * 12) / 47}%`);
    expect(card.querySelector('[data-action="analyze"]')).toBeNull();
  });

  it('lets a partial review be opened and re-run', () => {
    const root = document.createElement('div');
    renderClipList(root, {
      clips: [clip({ status: 'partial' })],
      jobs: {},
      options,
      settings: settings(),
      now: NOW,
      handlers: noopHandlers,
    });
    const card = root.querySelector('[data-clip]')!;
    expect(card.querySelector('[data-action="open"]')).not.toBeNull();
    expect(card.querySelector('[data-action="reanalyze"]')).not.toBeNull();
    expect(card.textContent).toContain('partial');
  });

  it('shows the error on a failed clip', () => {
    const root = document.createElement('div');
    renderClipList(root, {
      clips: [clip({ status: 'failed', error: 'OllamaError: refused' })],
      jobs: {},
      options,
      settings: settings(),
      now: NOW,
      handlers: noopHandlers,
    });
    expect(root.textContent).toContain('OllamaError: refused');
  });

  it('shows an empty state', () => {
    const root = document.createElement('div');
    renderClipList(root, {
      clips: [],
      jobs: {},
      options,
      settings: settings(),
      now: NOW,
      handlers: noopHandlers,
    });
    expect(root.textContent).toContain('No recordings');
  });
});

describe('renderPresetPicker', () => {
  it('marks the active preset and reports changes', () => {
    const root = document.createElement('div');
    const chosen: string[] = [];
    renderPresetPicker(root, 'first_minute', p => chosen.push(p));
    const buttons = Array.from(root.querySelectorAll('button'));
    expect(buttons.map(b => b.dataset['preset'])).toEqual(['full', 'first_minute', 'sampled']);
    expect(buttons[1]!.getAttribute('aria-pressed')).toBe('true');
    expect(buttons[0]!.getAttribute('aria-pressed')).toBe('false');
    buttons[2]!.click();
    expect(chosen).toEqual(['sampled']);
  });
});

describe('renderTimeline', () => {
  const built = report();

  it('draws coverage bands, ruler ticks and one pin per finding', () => {
    const root = document.createElement('div');
    renderTimeline(root, {
      markers: collectMarkers(built),
      coverage: coverageBands(built),
      durationS: built.recording.duration_s,
      selectedId: 'w1-f0',
      onSelect: () => undefined,
      onSeek: () => undefined,
    });
    expect(root.querySelectorAll('[data-band]')).toHaveLength(2);
    expect(root.querySelectorAll('[data-marker]')).toHaveLength(3);
    expect(root.querySelectorAll('[data-tick]').length).toBeGreaterThan(2);
    const pins = Array.from(root.querySelectorAll('[data-marker]')) as HTMLElement[];
    expect(parseFloat(pins[1]!.style.left)).toBeCloseTo(50.83, 1); // 305s of 600s
    expect(pins[1]!.classList.contains('selected')).toBe(true);
    expect(pins[0]!.textContent).toBe('1');
  });

  it('reports marker clicks', () => {
    const root = document.createElement('div');
    const picked: string[] = [];
    renderTimeline(root, {
      markers: collectMarkers(built),
      coverage: coverageBands(built),
      durationS: 600,
      selectedId: null,
      onSelect: id => picked.push(id),
      onSeek: () => undefined,
    });
    (root.querySelectorAll('[data-marker]')[2] as HTMLElement).click();
    expect(picked).toEqual(['w1-f1']);
  });

  it('labels each band with how much was reviewed', () => {
    const root = document.createElement('div');
    renderTimeline(root, {
      markers: [],
      coverage: coverageBands(built),
      durationS: 600,
      selectedId: null,
      onSelect: () => undefined,
      onSeek: () => undefined,
    });
    const band = root.querySelector('[data-band]') as HTMLElement;
    expect(band.title).toContain('Reviewed');
    expect(band.style.left).toBe('10%');
    expect(band.style.width).toBe('2%');
  });
});

describe('renderCoverageSummary', () => {
  it('states how much of the clip was reviewed', () => {
    const root = document.createElement('div');
    renderCoverageSummary(root, report());
    expect(root.textContent).toContain('4%'); // 24s of 600s
    expect(root.textContent).toContain('2 windows');
  });

  it('says so plainly when the whole clip was reviewed', () => {
    const root = document.createElement('div');
    const full = report({
      recording: { ...report().recording, duration_s: 24 },
    });
    renderCoverageSummary(root, full);
    expect(root.textContent).toContain('Whole clip reviewed');
  });

  it('collects review notes into a collapsed section', () => {
    const root = document.createElement('div');
    renderCoverageSummary(root, report({ warnings: ['file warning'] }));
    const details = root.querySelector('details')!;
    expect(details.textContent).toContain('Review notes (2)');
    expect(details.textContent).toContain('file warning');
    expect(details.textContent).toContain('window warning');
  });
});

describe('renderFindingList', () => {
  it('lists findings with number, timestamp and category, marking the selection', () => {
    const root = document.createElement('div');
    const picked: string[] = [];
    renderFindingList(root, collectMarkers(report()), 'w1-f0', id => picked.push(id));
    const rows = Array.from(root.querySelectorAll('[data-finding]'));
    expect(rows).toHaveLength(3);
    expect(rows[0]!.textContent).toContain('1:04');
    expect(rows[0]!.textContent).toContain('Held a wide angle.');
    expect(rows[1]!.getAttribute('aria-pressed')).toBe('true');
    (rows[2] as HTMLButtonElement).click();
    expect(picked).toEqual(['w1-f1']);
  });

  it('says when a review found nothing', () => {
    const root = document.createElement('div');
    renderFindingList(root, [], null, () => undefined);
    expect(root.textContent).toContain('No findings');
  });
});

describe('renderFindingCard', () => {
  it('leads with the fix, then the evidence and the reasoning', () => {
    const card = document.createElement('div');
    renderFindingCard(card, {
      marker: collectMarkers(report())[0]!,
      frameUrl: 'http://x/frames/w00_004.jpg',
      situationSummary: 'Entering A main with dash up.',
    });
    const text = card.textContent ?? '';
    expect(text).toContain('1:04');
    expect(text).toContain('positioning');
    expect(text).toContain('Crosshair placement');
    expect(text).toContain('Held a wide angle.');
    expect(text).toContain('Try instead');
    expect(text).toContain('Tighten the angle.');
    expect(text).toContain('What you could see');
    expect(text).toContain('enemy position');
    expect(text).toContain('Entering A main with dash up.');
    expect(text).toContain('80%');
    expect(card.querySelector('img')?.getAttribute('src')).toBe('http://x/frames/w00_004.jpg');
    // the fix comes before the reasoning
    expect(text.indexOf('Try instead')).toBeLessThan(text.indexOf('What you could see'));
  });

  it('shows what could not have been known when the model said so', () => {
    const card = document.createElement('div');
    const marker = {
      ...collectMarkers(report())[0]!,
      finding: finding({ information_revealed_later: 'Enemy behind box.' }),
    };
    renderFindingCard(card, { marker, frameUrl: null, situationSummary: null });
    expect(card.textContent).toContain("What you couldn't have known");
    expect(card.textContent).toContain('Enemy behind box.');
  });

  it('renders a placeholder when nothing is selected', () => {
    const card = document.createElement('div');
    renderFindingCard(card, { marker: null, frameUrl: null, situationSummary: null });
    expect(card.textContent).toContain('Select a finding');
  });
});

describe('renderContextBar', () => {
  it('renders rank, agent, map and side controls and reports changes', () => {
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
    const select = (field: string) =>
      bar.querySelector(`select[data-field="${field}"]`) as HTMLSelectElement;
    expect(select('rank').value).toBe('Gold');
    expect(select('agent').value).toBe('Jett');
    expect(select('map').value).toBe('');
    expect(Array.from(select('agent').options).map(o => o.value)).toEqual(['', 'Jett', 'Sova']);
    expect(Array.from(select('side').options).map(o => o.value)).toEqual(['', 'attack', 'defense']);
    select('map').value = 'Ascent';
    select('map').dispatchEvent(new Event('change'));
    const focus = bar.querySelector('input[data-field="focus"]') as HTMLInputElement;
    focus.value = 'entries';
    focus.dispatchEvent(new Event('change'));
    expect(changes).toEqual([
      ['map', 'Ascent'],
      ['focus', 'entries'],
    ]);
  });
});
