import { app, BrowserWindow, Menu, dialog } from 'electron';
import type { ChildProcess } from 'node:child_process';
import path from 'node:path';
import { buildMenuTemplate } from './menu';
import { buildSidecarCommand, findFreePort, startSidecar, waitForHealth } from './sidecar';

let sidecar: ChildProcess | null = null;

const sleep = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms));

const createWindow = (apiBaseUrl: string): void => {
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'round-review',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--api-base-url=${apiBaseUrl}`],
    },
  });
  Menu.setApplicationMenu(
    Menu.buildFromTemplate(
      buildMenuTemplate(() => window.webContents.send('open-settings'), process.platform),
    ),
  );
  void window.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
};

const boot = async (): Promise<void> => {
  const port = await findFreePort();
  const cmd = buildSidecarCommand({
    packaged: app.isPackaged,
    repoRoot: path.join(__dirname, '..', '..', '..'),
    resourcesPath: process.resourcesPath,
    port,
    platform: process.platform,
  });
  sidecar = startSidecar(cmd, line => process.stdout.write(`[sidecar] ${line}`));
  sidecar.on('exit', code => {
    if (!app.isReady() || BrowserWindow.getAllWindows().length === 0) return;
    dialog.showErrorBox('round-review', `The analysis service exited (code ${code ?? 'unknown'}).`);
  });
  const apiBaseUrl = `http://127.0.0.1:${port}`;
  await waitForHealth(`${apiBaseUrl}/api/health`, url => fetch(url), sleep, 60, 500);
  createWindow(apiBaseUrl);
};

app
  .whenReady()
  .then(boot)
  .catch(err => {
    dialog.showErrorBox(
      'round-review failed to start',
      err instanceof Error ? err.message : String(err),
    );
    app.quit();
  });

app.on('window-all-closed', () => app.quit());
app.on('will-quit', () => {
  sidecar?.kill();
  sidecar = null;
});
