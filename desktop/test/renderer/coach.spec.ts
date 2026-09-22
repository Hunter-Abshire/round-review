/**
 * @jest-environment jsdom
 */
import { renderCoachPanel, renderFindingList } from '../../src/renderer/dom';
import { collectMarkers } from '../../src/renderer/timeline';
import { habit, report, summary } from './fixtures';

const noop = () => undefined;

describe('renderCoachPanel', () => {
  it('leads with the verdict and the rank note', () => {
    const root = document.createElement('div');
    renderCoachPanel(root, summary(), noop);
    const text = root.textContent ?? '';
    expect(text).toContain('You re-peeked A Main');
    expect(text).toContain('At your rank');
    expect(root.querySelector('[data-verdict]')).not.toBeNull();
  });

  it('numbers the fixes, shows the count, and puts corrections before praise', () => {
    const root = document.createElement('div');
    renderCoachPanel(
      root,
      summary({
        focus: [habit(), habit({ check_id: 'trading.x', category: 'trading', count: 1 })],
      }),
      noop,
    );
    const items = Array.from(root.querySelectorAll('[data-focus]'));
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain('1');
    expect(items[0]!.textContent).toContain('3 times');
    expect(items[1]!.textContent).toContain('once');
    const text = root.textContent ?? '';
    expect(text.indexOf('What to work on')).toBeLessThan(text.indexOf('What worked'));
  });

  it('seeks to a fix when it is clicked', () => {
    const root = document.createElement('div');
    const sought: number[] = [];
    renderCoachPanel(root, summary(), t => sought.push(t));
    (root.querySelector('[data-focus]') as HTMLElement).click();
    expect(sought).toEqual([34]);
  });

  it('shows every timestamp of a recurring habit as its own jump', () => {
    const root = document.createElement('div');
    const sought: number[] = [];
    renderCoachPanel(root, summary(), t => sought.push(t));
    const jumps = Array.from(root.querySelectorAll('[data-jump]')) as HTMLElement[];
    expect(jumps).toHaveLength(3);
    jumps[2]!.click();
    expect(sought).toEqual([70]);
  });

  it('omits the praise section entirely when there is none', () => {
    const root = document.createElement('div');
    renderCoachPanel(root, summary({ strengths: [] }), noop);
    expect(root.textContent).not.toContain('What worked');
  });

  it('ends with the practice item', () => {
    const root = document.createElement('div');
    renderCoachPanel(root, summary(), noop);
    const practice = root.querySelector('[data-practice]')!;
    expect(practice.textContent).toContain('After you shoot once');
    expect(practice.textContent).toContain('Practice Range');
    expect(practice.textContent).toContain('fewer than 3 times');
    expect(root.lastElementChild).toBe(practice);
  });

  it('flags hindsight findings separately from the fixes', () => {
    const root = document.createElement('div');
    renderCoachPanel(
      root,
      summary({
        hindsight: [habit({ information_revealed_later: 'An enemy was behind the box.' })],
      }),
      noop,
    );
    const section = root.querySelector('[data-hindsight]')!;
    expect(section.textContent).toContain('An enemy was behind the box.');
  });

  it('lists what it saw but is not asking you to fix', () => {
    const root = document.createElement('div');
    renderCoachPanel(
      root,
      summary({
        also_seen: [
          { check_id: 'economy.buy', check_label: 'Economy / buy', category: 'economy', count: 1 },
        ],
      }),
      noop,
    );
    expect(root.querySelector('[data-also-seen]')?.textContent).toContain('economy');
  });

  it('says plainly when a review found nothing', () => {
    const root = document.createElement('div');
    renderCoachPanel(
      root,
      summary({
        verdict: 'Nothing worth acting on came out of the 5 reviewed windows.',
        focus: [],
        strengths: [],
        practice: null,
      }),
      noop,
    );
    expect(root.textContent).toContain('Nothing worth acting on');
    expect(root.textContent).not.toContain('What to work on');
    expect(root.querySelector('[data-practice]')).toBeNull();
  });
});

describe('renderFindingList grouped by category', () => {
  it('groups findings under a category heading rather than listing them flat', () => {
    const root = document.createElement('div');
    renderFindingList(root, collectMarkers(report()), null, noop);
    const headings = Array.from(root.querySelectorAll('[data-category]')).map(
      h => h.textContent ?? '',
    );
    expect(headings.length).toBeGreaterThan(0);
    expect(headings.some(h => h.includes('positioning'))).toBe(true);
    expect(headings.some(h => h.includes('utility'))).toBe(true);
    // every finding still gets a row
    expect(root.querySelectorAll('[data-finding]')).toHaveLength(3);
  });

  it('shows the count next to each category', () => {
    const root = document.createElement('div');
    renderFindingList(root, collectMarkers(report()), null, noop);
    const positioning = Array.from(root.querySelectorAll('[data-category]')).find(h =>
      (h.textContent ?? '').includes('positioning'),
    );
    expect(positioning?.textContent).toContain('2');
  });
});

describe('focus card trends', () => {
  it('says a persistent habit has come up every recent match', () => {
    const root = document.createElement('div');
    const base = summary();
    renderCoachPanel(
      root,
      { ...base, focus: base.focus.map(h => ({ ...h, trend: 'persistent' as const })) },
      () => undefined,
    );
    expect(root.textContent).toContain('every recent match');
  });

  it('shows no trend at all when there is no history', () => {
    const root = document.createElement('div');
    renderCoachPanel(root, summary(), () => undefined);
    expect(root.querySelector('.trend')).toBeNull();
  });
});
