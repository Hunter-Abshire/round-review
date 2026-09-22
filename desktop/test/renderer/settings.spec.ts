/**
 * @jest-environment jsdom
 */
import { renderSettings, type SettingsHandlers } from '../../src/renderer/dom';
import { configDocument, configField } from './fixtures';

const noop: SettingsHandlers = {
  onChange: () => undefined,
  onSave: () => undefined,
  onClose: () => undefined,
  onToggleAdvanced: () => undefined,
};

const render = (
  overrides: Parameters<typeof renderSettings>[1] = {},
  handlers: SettingsHandlers = noop,
): HTMLElement => {
  const root = document.createElement('div');
  renderSettings(
    root,
    { document: configDocument(), edits: {}, saving: false, showAdvanced: false, ...overrides },
    handlers,
  );
  return root;
};

describe('renderSettings', () => {
  it('groups settings under their headings in order', () => {
    const headings = Array.from(render().querySelectorAll('[data-group]')).map(h => h.textContent);
    expect(headings).toEqual(['Recordings', 'How much to review']);
  });

  it('renders a choice as a dropdown of its options', () => {
    const select = render().querySelector('select[data-setting="coverage"]') as HTMLSelectElement;
    expect(Array.from(select.options).map(o => o.value)).toEqual(['full', 'sampled']);
    expect(select.value).toBe('full');
  });

  it('renders a number with its bounds and unit', () => {
    const root = render();
    const input = root.querySelector('input[data-setting="max_span_s"]') as HTMLInputElement;
    expect(input.type).toBe('number');
    expect(input.min).toBe('0');
    expect(input.value).toBe('0');
    expect(root.textContent).toContain('seconds');
  });

  it('renders a boolean as a checkbox', () => {
    const root = render({
      document: configDocument({
        fields: [configField({ name: 'situation_pass', kind: 'bool', choices: [], value: true })],
      }),
    });
    const box = root.querySelector('input[data-setting="situation_pass"]') as HTMLInputElement;
    expect(box.type).toBe('checkbox');
    expect(box.checked).toBe(true);
  });

  it('shows the help for every setting', () => {
    expect(render().textContent).toContain('Where Outplayed writes your clips.');
  });

  it('hides advanced settings until they are asked for', () => {
    expect(render().querySelector('[data-setting="hud_threshold"]')).toBeNull();
    expect(
      render({ showAdvanced: true }).querySelector('[data-setting="hud_threshold"]'),
    ).not.toBeNull();
  });

  it('reports a change without saving it', () => {
    const changes: Array<[string, unknown]> = [];
    const root = render({}, { ...noop, onChange: (name, value) => changes.push([name, value]) });
    const select = root.querySelector('select[data-setting="coverage"]') as HTMLSelectElement;
    select.value = 'sampled';
    select.dispatchEvent(new Event('change'));
    expect(changes).toEqual([['coverage', 'sampled']]);
  });

  it('shows an edited value and marks it unsaved', () => {
    const root = render({ edits: { coverage: 'sampled' } });
    const select = root.querySelector('select[data-setting="coverage"]') as HTMLSelectElement;
    expect(select.value).toBe('sampled');
    expect(root.querySelector('[data-unsaved]')).not.toBeNull();
  });

  it('only enables saving when something has changed', () => {
    expect((render().querySelector('[data-action="save"]') as HTMLButtonElement).disabled).toBe(
      true,
    );
    const dirty = render({ edits: { coverage: 'sampled' } });
    expect((dirty.querySelector('[data-action="save"]') as HTMLButtonElement).disabled).toBe(false);
  });

  it('sends the edits when saved', () => {
    const saved: unknown[] = [];
    const root = render(
      { edits: { coverage: 'sampled' } },
      { ...noop, onSave: () => saved.push(true) },
    );
    (root.querySelector('[data-action="save"]') as HTMLButtonElement).click();
    expect(saved).toHaveLength(1);
  });

  it('warns when an environment variable has taken a setting over', () => {
    const root = render({
      document: configDocument({
        fields: [configField({ overridden_by_env: 'ROUND_REVIEW_COVERAGE' })],
      }),
    });
    const warning = root.querySelector('[data-env-override]')!;
    expect(warning.textContent).toContain('ROUND_REVIEW_COVERAGE');
    expect((root.querySelector('[data-setting="coverage"]') as HTMLSelectElement).disabled).toBe(
      true,
    );
  });

  it('shows where the file lives, for anyone who wants to edit it directly', () => {
    expect(render().textContent).toContain('config.toml');
  });

  it('says it is saving while it saves', () => {
    const root = render({ edits: { coverage: 'sampled' }, saving: true });
    const save = root.querySelector('[data-action="save"]') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(save.textContent?.toLowerCase()).toContain('saving');
  });

  it('closes', () => {
    const closed: unknown[] = [];
    const root = render({}, { ...noop, onClose: () => closed.push(true) });
    (root.querySelector('[data-action="close"]') as HTMLButtonElement).click();
    expect(closed).toHaveLength(1);
  });
});
