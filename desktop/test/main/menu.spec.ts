import { buildMenuTemplate } from '../../src/main/menu';

describe('buildMenuTemplate', () => {
  const template = () => buildMenuTemplate(() => undefined, 'darwin');

  it('has a File menu with Settings in it', () => {
    const file = template().find(m => m.label === 'File');
    expect(file).toBeDefined();
    const labels = (file?.submenu ?? []).map(item => item.label ?? item.role);
    expect(labels).toContain('Settings');
  });

  it('gives Settings the platform shortcut', () => {
    const settings = buildMenuTemplate(() => undefined, 'win32')
      .find(m => m.label === 'File')
      ?.submenu?.find(i => i.label === 'Settings');
    expect(settings?.accelerator).toBe('Ctrl+,');
    const mac = template()
      .find(m => m.label === 'File')
      ?.submenu?.find(i => i.label === 'Settings');
    expect(mac?.accelerator).toBe('Cmd+,');
  });

  it('calls back when Settings is chosen', () => {
    const opened: string[] = [];
    const settings = buildMenuTemplate(() => opened.push('settings'), 'win32')
      .find(m => m.label === 'File')
      ?.submenu?.find(i => i.label === 'Settings');
    // Electron types click with three arguments; ours ignores all of them.
    const click = settings?.click as (() => void) | undefined;
    click?.();
    expect(opened).toEqual(['settings']);
  });

  it('keeps a way to quit and the developer tools', () => {
    const roles = template().flatMap(m => (m.submenu ?? []).map(i => i.role));
    expect(roles).toContain('quit');
    expect(roles).toContain('toggleDevTools');
  });
});
