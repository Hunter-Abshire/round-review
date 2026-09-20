import { spawn, type ChildProcess } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';

export interface SidecarOptions {
  packaged: boolean;
  repoRoot: string;
  resourcesPath: string;
  port: number;
  platform: NodeJS.Platform;
}

export interface SidecarCommand {
  command: string;
  args: string[];
}

/** Dev: the repo's .venv entry point. Packaged: a PyInstaller binary next to the app. */
export const buildSidecarCommand = (opts: SidecarOptions): SidecarCommand => {
  const win = opts.platform === 'win32';
  // Join with the target platform's separator, not the host's, so tests are portable.
  const join = win ? path.win32.join : path.posix.join;
  const binary = win ? 'round-review.exe' : 'round-review';
  const command = opts.packaged
    ? join(opts.resourcesPath, binary)
    : join(opts.repoRoot, '.venv', win ? 'Scripts' : 'bin', binary);
  return { command, args: ['serve', '--port', String(opts.port)] };
};

export type HealthFetch = (url: string) => Promise<{ ok: boolean; status: number }>;
export type Sleep = (ms: number) => Promise<void>;

export const waitForHealth = async (
  url: string,
  fetchFn: HealthFetch,
  sleep: Sleep,
  attempts: number,
  intervalMs: number,
): Promise<void> => {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const response = await fetchFn(url);
      if (response.ok) return;
    } catch {
      // not up yet
    }
    if (i < attempts - 1) await sleep(intervalMs);
  }
  throw new Error(`sidecar did not become healthy after ${attempts} attempts at ${url}`);
};

export const findFreePort = (): Promise<number> =>
  new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (address === null || typeof address === 'string') {
        reject(new Error('could not allocate a port'));
        return;
      }
      server.close(() => resolve(address.port));
    });
  });

export const startSidecar = (cmd: SidecarCommand, onLog: (line: string) => void): ChildProcess => {
  const child = spawn(cmd.command, cmd.args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  child.stdout?.on('data', (chunk: Buffer) => onLog(chunk.toString()));
  child.stderr?.on('data', (chunk: Buffer) => onLog(chunk.toString()));
  return child;
};
