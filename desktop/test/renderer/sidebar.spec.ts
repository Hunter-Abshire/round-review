/**
 * @jest-environment jsdom
 */
import { renderFindingDetail, renderSideTabs, renderGroupedFindings } from '../../src/renderer/dom';
import { collectMarkers } from '../../src/renderer/timeline';
import { finding, report } from './fixtures';

describe('renderSideTabs', () => {
  it('offers findings, coach and ask, marking the active one', () => {
    const root = document.createElement('div');
    const picked: string[] = [];
    renderSideTabs(root, 'ask', 22, p => picked.push(p));
    const tabs = Array.from(root.querySelectorAll('[data-tab]')) as HTMLElement[];
    expect(tabs.map(t => t.dataset['tab'])).toEqual(['findings', 'coach', 'ask']);
    expect(tabs[2]!.getAttribute('aria-pressed')).toBe('true');
    expect(tabs[0]!.textContent).toContain('22');
    tabs[1]!.click();
    expect(picked).toEqual(['coach']);
  });
});

describe('renderGroupedFindings', () => {
  const markers = collectMarkers(report());

  it('puts every finding of a category under one heading', () => {
    const root = document.createElement('div');
    renderGroupedFindings(root, markers, null, () => undefined);
    const headings = Array.from(root.querySelectorAll('[data-category]')).map(h => h.textContent);
    // three findings: two positioning, one utility, so two headings, not four
    expect(headings).toHaveLength(2);
    expect(headings.filter(h => h?.includes('positioning'))).toHaveLength(1);
  });

  it('labels a finding with its round when the clock was read', () => {
    const root = document.createElement('div');
    const withRound = markers.map(m => ({ ...m, roundIndex: 7 }));
    renderGroupedFindings(root, withRound, null, () => undefined);
    expect(root.querySelector('.finding-time')?.textContent).toContain('Round 7');
  });

  it('falls back to the timestamp alone when there is no round', () => {
    const root = document.createElement('div');
    renderGroupedFindings(root, markers, null, () => undefined);
    expect(root.querySelector('.finding-time')?.textContent).not.toContain('Round');
  });

  it('counts each category once', () => {
    const root = document.createElement('div');
    renderGroupedFindings(root, markers, null, () => undefined);
    const positioning = Array.from(root.querySelectorAll('[data-category]')).find(h =>
      (h.textContent ?? '').includes('positioning'),
    );
    expect(positioning?.textContent).toContain('2');
  });

  it('keeps findings in playback order inside a category', () => {
    const root = document.createElement('div');
    renderGroupedFindings(root, markers, null, () => undefined);
    const times = Array.from(root.querySelectorAll('[data-finding] .finding-time')).map(
      n => n.textContent,
    );
    expect(times).toEqual(['1:04', '5:10', '5:05']); // positioning group, then utility
  });

  it('reports a click', () => {
    const root = document.createElement('div');
    const picked: string[] = [];
    renderGroupedFindings(root, markers, null, id => picked.push(id));
    (root.querySelectorAll('[data-finding]')[1] as HTMLElement).click();
    expect(picked).toHaveLength(1);
  });

  it('says so when a review found nothing', () => {
    const root = document.createElement('div');
    renderGroupedFindings(root, [], null, () => undefined, 'the model skipped everything');
    expect(root.textContent).toContain('the model skipped everything');
  });
});

describe('renderFindingDetail', () => {
  const marker = collectMarkers(report())[0]!;

  it('shows the finding beside the video, with a way back to the list', () => {
    const root = document.createElement('div');
    const back: string[] = [];
    renderFindingDetail(
      root,
      { marker, frameUrl: null, ordinalOf: 1, total: 3 },
      {
        onBack: () => back.push('back'),
        onStep: () => undefined,
      },
    );
    expect(root.textContent).toContain('1:04');
    expect(root.textContent).toContain('Held a wide angle.');
    expect(root.textContent).toContain('Try instead');
    (root.querySelector('[data-action="back"]') as HTMLButtonElement).click();
    expect(back).toEqual(['back']);
  });

  it('steps between findings without going back to the list', () => {
    const root = document.createElement('div');
    const steps: number[] = [];
    renderFindingDetail(
      root,
      { marker, frameUrl: null, ordinalOf: 2, total: 3 },
      {
        onBack: () => undefined,
        onStep: d => steps.push(d),
      },
    );
    expect(root.textContent).toContain('2 of 3');
    (root.querySelector('[data-action="next"]') as HTMLButtonElement).click();
    (root.querySelector('[data-action="prev"]') as HTMLButtonElement).click();
    expect(steps).toEqual([1, -1]);
  });

  it('notes when the model drew on the frame', () => {
    const root = document.createElement('div');
    const drawn = {
      ...marker,
      finding: finding({
        focus: [
          {
            kind: 'box' as const,
            label: 'the exposed angle',
            x: 0.1,
            y: 0.1,
            w: 0.2,
            h: 0.2,
            x2: 0,
            y2: 0,
          },
        ],
      }),
    };
    renderFindingDetail(
      root,
      { marker: drawn, frameUrl: null, ordinalOf: 1, total: 1 },
      {
        onBack: () => undefined,
        onStep: () => undefined,
      },
    );
    expect(root.textContent).toContain('marked on the video');
  });
});

describe('rating a finding', () => {
  const marker = collectMarkers(report())[0]!;

  it('offers yes and no when rating is wired up', () => {
    const root = document.createElement('div');
    const rated: string[] = [];
    renderFindingDetail(
      root,
      { marker, frameUrl: null, ordinalOf: 1, total: 1 },
      { onBack: () => undefined, onStep: () => undefined, onRate: v => rated.push(v) },
    );
    const buttons = Array.from(root.querySelectorAll('[data-verdict]')) as HTMLElement[];
    expect(buttons.map(b => b.dataset['verdict'])).toEqual(['useful', 'wrong']);
    buttons[1]!.click();
    expect(rated).toEqual(['wrong']);
  });

  it('marks the verdict the player already gave', () => {
    const root = document.createElement('div');
    renderFindingDetail(
      root,
      { marker, frameUrl: null, ordinalOf: 1, total: 1, verdict: 'useful' },
      { onBack: () => undefined, onStep: () => undefined, onRate: () => undefined },
    );
    const chosen = root.querySelector('[data-verdict="useful"]');
    expect(chosen?.getAttribute('aria-pressed')).toBe('true');
  });

  it('draws nothing when rating is not wired up', () => {
    const root = document.createElement('div');
    renderFindingDetail(
      root,
      { marker, frameUrl: null, ordinalOf: 1, total: 1 },
      { onBack: () => undefined, onStep: () => undefined },
    );
    expect(root.querySelector('[data-verdict]')).toBeNull();
  });
});
