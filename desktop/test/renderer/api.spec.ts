import { createApi, type FetchLike } from '../../src/renderer/api';

const fakeFetch = (
  status: number,
  body: unknown,
): { fetch: FetchLike; calls: Array<[string, RequestInit | undefined]> } => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fetch: FetchLike = async (url, init) => {
    calls.push([url, init]);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  };
  return { fetch, calls };
};

describe('createApi', () => {
  const base = 'http://127.0.0.1:8765';

  it('getClips hits /api/clips', async () => {
    const { fetch, calls } = fakeFetch(200, { clips: [], warning: null });
    const api = createApi(base, fetch);
    await expect(api.getClips()).resolves.toEqual({ clips: [], warning: null });
    expect(calls[0]?.[0]).toBe(`${base}/api/clips`);
  });

  it('submitJob posts JSON', async () => {
    const { fetch, calls } = fakeFetch(202, { id: 'j' });
    await createApi(base, fetch).submitJob('/v/a.mp4');
    const [url, init] = calls[0]!;
    expect(url).toBe(`${base}/api/jobs`);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ path: '/v/a.mp4' });
  });

  it('getJob and getReport build paths', async () => {
    const { fetch, calls } = fakeFetch(200, {});
    const api = createApi(base, fetch);
    await api.getJob('j1');
    await api.getReport('0123456789abcdef');
    expect(calls.map(c => c[0])).toEqual([
      `${base}/api/jobs/j1`,
      `${base}/api/reports/0123456789abcdef`,
    ]);
  });

  it('builds media urls without fetching', () => {
    const api = createApi(base, fakeFetch(200, {}).fetch);
    expect(api.videoUrl('k')).toBe(`${base}/api/media/k/video`);
    expect(api.frameUrl('k', 'frames/w00_004.jpg')).toBe(`${base}/api/media/k/frames/w00_004.jpg`);
  });

  it('throws with status and detail on non-2xx', async () => {
    const { fetch } = fakeFetch(404, { detail: 'clip not found' });
    await expect(createApi(base, fetch).getJob('x')).rejects.toThrow('404: clip not found');
  });
});
