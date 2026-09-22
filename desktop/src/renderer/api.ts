import type {
  Clip,
  ConfigDocument,
  Job,
  Knowledge,
  PlayerContext,
  Report,
  ReviewOptions,
  Settings,
} from '../shared/types';

export interface FetchResponseLike {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}
export type FetchLike = (url: string, init?: RequestInit) => Promise<FetchResponseLike>;

export interface ClipsResponse {
  clips: Clip[];
  warning: string | null;
}

export interface Api {
  getClips: () => Promise<ClipsResponse>;
  submitJob: (
    path: string,
    context: PlayerContext,
    options: ReviewOptions,
    force?: boolean,
  ) => Promise<Job>;
  getKnowledge: () => Promise<Knowledge>;
  getSettings: () => Promise<Settings>;
  getConfig: () => Promise<ConfigDocument>;
  saveConfig: (values: Record<string, unknown>) => Promise<{ saved: boolean }>;
  ask: (
    path: string,
    startS: number,
    endS: number,
    question: string,
    context: PlayerContext,
  ) => Promise<Job>;
  getJob: (id: string) => Promise<Job>;
  getReport: (key: string) => Promise<Report>;
  videoUrl: (key: string) => string;
  frameUrl: (key: string, evidenceFrame: string) => string;
}

const detailOf = async (response: FetchResponseLike): Promise<string> => {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    // fall through to the status text
  }
  return `HTTP ${response.status}`;
};

export const createApi = (baseUrl: string, fetchFn: FetchLike): Api => {
  const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetchFn(`${baseUrl}${path}`, init);
    if (!response.ok) throw new Error(`${response.status}: ${await detailOf(response)}`);
    return (await response.json()) as T;
  };
  return {
    getClips: () => request<ClipsResponse>('/api/clips'),
    submitJob: (path, context, options, force = false) =>
      request<Job>('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          context,
          force,
          coverage: options.coverage,
          max_span_s: options.max_span_s,
          max_windows: options.max_windows,
        }),
      }),
    getKnowledge: () => request<Knowledge>('/api/knowledge'),
    getSettings: () => request<Settings>('/api/settings'),
    getConfig: () => request<ConfigDocument>('/api/config'),
    saveConfig: values =>
      request<{ saved: boolean }>('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values }),
      }),
    ask: (path, startS, endS, question, context) =>
      request<Job>('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, start_s: startS, end_s: endS, question, context }),
      }),
    getJob: id => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
    getReport: key => request<Report>(`/api/reports/${encodeURIComponent(key)}`),
    videoUrl: key => `${baseUrl}/api/media/${encodeURIComponent(key)}/video`,
    // evidence_frame is "frames/<name>" relative to the report dir; the API takes just the name
    frameUrl: (key, evidenceFrame) =>
      `${baseUrl}/api/media/${encodeURIComponent(key)}/frames/${encodeURIComponent(evidenceFrame.replace(/^frames\//, ''))}`,
  };
};
