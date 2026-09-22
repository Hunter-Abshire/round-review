/**
 * @jest-environment jsdom
 */
import { renderAskBox, renderAnswers } from '../../src/renderer/dom';
import { SUGGESTED_QUESTIONS } from '../../src/renderer/state';
import { answer, askState } from './fixtures';

describe('renderAskBox', () => {
  const handlers = { onAsk: () => undefined, onRangeChange: () => undefined };

  it('prefills the range around the playhead and shows it in clock terms', () => {
    const root = document.createElement('div');
    renderAskBox(root, { ...askState(), start_s: 100, end_s: 112 }, handlers);
    const start = root.querySelector('[data-field="start"]') as HTMLInputElement;
    const end = root.querySelector('[data-field="end"]') as HTMLInputElement;
    expect(start.value).toBe('100');
    expect(end.value).toBe('112');
    expect(root.textContent).toContain('1:40');
    expect(root.textContent).toContain('1:52');
  });

  it('offers suggested questions that fill the box when clicked', () => {
    const root = document.createElement('div');
    const asked: string[] = [];
    renderAskBox(root, askState(), { ...handlers, onAsk: q => asked.push(q) });
    const chips = Array.from(root.querySelectorAll('[data-suggestion]')) as HTMLElement[];
    expect(chips.length).toBe(SUGGESTED_QUESTIONS.length);
    expect(chips[0]!.textContent).toBe(SUGGESTED_QUESTIONS[0]);
    chips[0]!.click();
    const input = root.querySelector('[data-field="question"]') as HTMLTextAreaElement;
    expect(input.value).toBe(SUGGESTED_QUESTIONS[0]);
    expect(asked).toEqual([]); // filling the box does not send it
  });

  it('sends the typed question', () => {
    const root = document.createElement('div');
    const asked: string[] = [];
    renderAskBox(root, askState(), { ...handlers, onAsk: q => asked.push(q) });
    const input = root.querySelector('[data-field="question"]') as HTMLTextAreaElement;
    input.value = '  Why did I lose that duel?  ';
    (root.querySelector('[data-action="ask"]') as HTMLButtonElement).click();
    expect(asked).toEqual(['Why did I lose that duel?']);
  });

  it('refuses to send an empty question', () => {
    const root = document.createElement('div');
    const asked: string[] = [];
    renderAskBox(root, askState(), { ...handlers, onAsk: q => asked.push(q) });
    (root.querySelector('[data-action="ask"]') as HTMLButtonElement).click();
    expect(asked).toEqual([]);
  });

  it('reports a range change', () => {
    const root = document.createElement('div');
    const ranges: Array<[number, number]> = [];
    renderAskBox(root, askState(), {
      ...handlers,
      onRangeChange: (a, b) => ranges.push([a, b]),
    });
    const end = root.querySelector('[data-field="end"]') as HTMLInputElement;
    end.value = '130';
    end.dispatchEvent(new Event('change'));
    expect(ranges).toEqual([[100, 130]]);
  });

  it('disables itself and says so while an answer is coming', () => {
    const root = document.createElement('div');
    renderAskBox(root, { ...askState(), pending: true }, handlers);
    expect((root.querySelector('[data-action="ask"]') as HTMLButtonElement).disabled).toBe(true);
    expect(root.textContent?.toLowerCase()).toContain('thinking');
  });

  it('warns when the range is longer than the limit', () => {
    const root = document.createElement('div');
    renderAskBox(root, { ...askState(), start_s: 0, end_s: 300, maxSpanS: 60 }, handlers);
    expect(root.querySelector('[data-range-warning]')?.textContent).toContain('1:00');
  });
});

describe('renderAnswers', () => {
  it('shows the question, the answer and the alternatives', () => {
    const root = document.createElement('div');
    renderAnswers(root, [answer()], () => undefined);
    const text = root.textContent ?? '';
    expect(text).toContain('How could I have used utility here?');
    expect(text).toContain('1:40');
    expect(text).toContain('You pushed that angle');
    expect(text).toContain('Smoke the far angle first.');
    expect(text).toContain('It halves the exposure.');
    expect(text).toContain('What you could see');
  });

  it('puts the newest answer first', () => {
    const root = document.createElement('div');
    renderAnswers(
      root,
      [answer({ question: 'older' }), answer({ question: 'newer' })],
      () => undefined,
    );
    const questions = Array.from(root.querySelectorAll('[data-answer-question]')).map(
      n => n.textContent,
    );
    expect(questions[0]).toContain('newer');
  });

  it('jumps back to the moment that was asked about', () => {
    const root = document.createElement('div');
    const sought: number[] = [];
    renderAnswers(root, [answer()], t => sought.push(t));
    (root.querySelector('[data-answer-jump]') as HTMLElement).click();
    expect(sought).toEqual([100]);
  });

  it('marks an answer the model could not give', () => {
    const root = document.createElement('div');
    renderAnswers(
      root,
      [
        answer({
          answerable: false,
          answer: 'The frames do not show your abilities.',
          alternatives: [],
        }),
      ],
      () => undefined,
    );
    expect(root.querySelector('[data-unanswerable]')).not.toBeNull();
    expect(root.textContent).toContain('do not show your abilities');
  });

  it('shows what could not have been known when the model says so', () => {
    const root = document.createElement('div');
    renderAnswers(
      root,
      [answer({ what_you_could_not_know: 'An enemy was behind it.' })],
      () => undefined,
    );
    expect(root.textContent).toContain("What you couldn't have known");
    expect(root.textContent).toContain('An enemy was behind it.');
  });

  it('says nothing at all before the first question', () => {
    const root = document.createElement('div');
    renderAnswers(root, [], () => undefined);
    expect(root.childElementCount).toBe(0);
  });
});
