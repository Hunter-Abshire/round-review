import { buildSidecarCommand, waitForHealth } from '../../src/main/sidecar';

describe('buildSidecarCommand', () => {
  it('uses the repo venv in development', () => {
    const cmd = buildSidecarCommand({
      packaged: false,
      repoRoot: '/repo',
      resourcesPath: '/res',
      port: 8765,
      platform: 'darwin',
    });
    expect(cmd).toEqual({
      command: '/repo/.venv/bin/round-review',
      args: ['serve', '--port', '8765'],
    });
  });
  it('uses the Scripts dir on Windows in development', () => {
    const cmd = buildSidecarCommand({
      packaged: false,
      repoRoot: 'C:\\repo',
      resourcesPath: '',
      port: 1,
      platform: 'win32',
    });
    expect(cmd.command).toBe('C:\\repo\\.venv\\Scripts\\round-review.exe');
  });
  it('uses the bundled binary when packaged', () => {
    const mac = buildSidecarCommand({
      packaged: true,
      repoRoot: '',
      resourcesPath: '/App/Resources',
      port: 9000,
      platform: 'darwin',
    });
    expect(mac.command).toBe('/App/Resources/round-review');
    const win = buildSidecarCommand({
      packaged: true,
      repoRoot: '',
      resourcesPath: 'C:\\App\\resources',
      port: 9000,
      platform: 'win32',
    });
    expect(win.command).toBe('C:\\App\\resources\\round-review.exe');
    expect(win.args).toEqual(['serve', '--port', '9000']);
  });
});

describe('waitForHealth', () => {
  it('resolves once the health endpoint answers 200', async () => {
    let n = 0;
    const fetchFn = async () => {
      n += 1;
      if (n < 3) throw new Error('ECONNREFUSED');
      return { ok: true, status: 200 };
    };
    const slept: number[] = [];
    await expect(
      waitForHealth(
        'http://127.0.0.1:1/api/health',
        fetchFn,
        ms => {
          slept.push(ms);
          return Promise.resolve();
        },
        5,
        100,
      ),
    ).resolves.toBeUndefined();
    expect(n).toBe(3);
    expect(slept).toEqual([100, 100]);
  });
  it('rejects after the attempt budget', async () => {
    const fetchFn = async () => ({ ok: false, status: 503 });
    await expect(waitForHealth('u', fetchFn, () => Promise.resolve(), 2, 0)).rejects.toThrow(
      'sidecar did not become healthy',
    );
  });
});
