import { contextBridge } from 'electron';

// The main process passes the sidecar URL as an additional argument; nothing else crosses.
const arg = process.argv.find(a => a.startsWith('--api-base-url='));
const apiBaseUrl = arg ? arg.slice('--api-base-url='.length) : 'http://127.0.0.1:8765';

contextBridge.exposeInMainWorld('roundReview', { apiBaseUrl });
