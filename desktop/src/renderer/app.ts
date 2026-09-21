// Renderer entry point. Bundled by esbuild into dist/renderer/app.js; no Node access here.
import { createApi, type Api } from './api';
import { renderClipList, renderContextBar, renderFindingCard, renderMarkers } from './dom';
import { initialState, reduce, type Action, type State } from './state';
import { nearestMarker } from './timeline';

declare global {
  interface Window {
    roundReview: { apiBaseUrl: string };
  }
}

const POLL_MS = 2000;
const CLIP_REFRESH_MS = 10000;
const MARKER_TOLERANCE_S = 1.5;

const byId = (id: string): HTMLElement => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing element #${id}`);
  return node;
};

const run = (api: Api): void => {
  let state: State = initialState;
  const listView = byId('list-view');
  const contextBar = byId('context-bar');
  const reviewView = byId('review-view');
  const clipsRoot = byId('clips');
  const banner = byId('banner');
  const video = byId('video') as HTMLVideoElement;
  const track = byId('track');
  const card = byId('finding');
  const title = byId('review-title');
  const situationLine = byId('situation');

  const dispatch = (action: Action): void => {
    state = reduce(state, action);
    render();
  };

  const render = (): void => {
    banner.textContent = state.error ?? state.warning ?? '';
    banner.hidden = banner.textContent === '';
    listView.hidden = state.view !== 'list';
    reviewView.hidden = state.view !== 'review';
    if (state.view === 'list') {
      if (state.knowledge) {
        renderContextBar(contextBar, state.context, state.knowledge, (field, value) =>
          dispatch({ type: 'context_changed', field, value }),
        );
      }
      renderClipList(clipsRoot, state.clips, state.jobs, { onAnalyze: analyze, onOpen: open });
      return;
    }
    const report = state.report;
    if (!report || !state.reviewKey) return;
    title.textContent = `${report.recording.name} · ${state.markers.length} finding(s) · ${report.model}`;
    renderMarkers(
      track,
      state.markers,
      report.recording.duration_s,
      state.selectedMarkerId,
      select,
    );
    const marker = state.markers.find(m => m.id === state.selectedMarkerId) ?? null;
    const windowInfo = marker
      ? report.windows.find(w => w.index === marker.windowIndex)
      : undefined;
    situationLine.textContent = windowInfo?.situation?.summary ?? '';
    situationLine.hidden = situationLine.textContent === '';
    const frame = marker?.finding.evidence_frame
      ? api.frameUrl(state.reviewKey, marker.finding.evidence_frame)
      : null;
    renderFindingCard(card, marker?.finding ?? null, frame);
  };

  const fail = (err: unknown): void =>
    dispatch({ type: 'error', message: err instanceof Error ? err.message : String(err) });

  const refreshClips = async (): Promise<void> => {
    try {
      const { clips, warning } = await api.getClips();
      dispatch({ type: 'clips_loaded', clips, warning });
    } catch (err) {
      fail(err);
    }
  };

  const analyze = async (key: string): Promise<void> => {
    const clip = state.clips.find(c => c.key === key);
    if (!clip) return;
    try {
      dispatch({ type: 'job_submitted', job: await api.submitJob(clip.path, state.context) });
    } catch (err) {
      fail(err);
    }
  };

  const open = async (key: string): Promise<void> => {
    try {
      const report = await api.getReport(key);
      dispatch({ type: 'report_loaded', key, report });
      video.src = api.videoUrl(key);
      video.load();
    } catch (err) {
      fail(err);
    }
  };

  const select = (id: string): void => {
    dispatch({ type: 'marker_selected', id });
    const marker = state.markers.find(m => m.id === id);
    if (marker) video.currentTime = marker.timestamp_s;
  };

  const pollJobs = async (): Promise<void> => {
    for (const id of state.activeJobIds) {
      try {
        dispatch({ type: 'job_updated', job: await api.getJob(id) });
      } catch (err) {
        fail(err);
      }
    }
  };

  byId('back').addEventListener('click', () => {
    video.pause();
    video.removeAttribute('src');
    video.load();
    dispatch({ type: 'back_to_list' });
    void refreshClips();
  });
  byId('refresh').addEventListener('click', () => void refreshClips());

  // Highlight the marker we are passing while the video plays.
  video.addEventListener('timeupdate', () => {
    const near = nearestMarker(state.markers, video.currentTime, MARKER_TOLERANCE_S);
    if (near && near.id !== state.selectedMarkerId)
      dispatch({ type: 'marker_selected', id: near.id });
  });
  video.addEventListener('error', () => {
    fail(
      'This clip cannot be played in the app (unsupported codec, likely HEVC). Findings are still listed below.',
    );
  });

  const loadKnowledge = async (): Promise<void> => {
    try {
      dispatch({ type: 'knowledge_loaded', knowledge: await api.getKnowledge() });
    } catch (err) {
      fail(err);
    }
  };

  window.setInterval(() => void pollJobs(), POLL_MS);
  window.setInterval(() => {
    if (state.view === 'list' && state.activeJobIds.length === 0) void refreshClips();
  }, CLIP_REFRESH_MS);
  void loadKnowledge();
  void refreshClips();
};

run(createApi(window.roundReview.apiBaseUrl, (url, init) => fetch(url, init)));
