// Renderer entry point. Bundled by esbuild into dist/renderer/app.js; no Node access here.
import { createApi, type Api } from './api';
import {
  diagnosisOf,
  renderClipList,
  renderContextBar,
  renderCoverageSummary,
  renderFindingCard,
  renderFindingList,
  renderHudHint,
  renderPresetPicker,
  renderTimeline,
} from './dom';
import { initialState, reduce, type Action, type Preset, type State } from './state';
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
  const reviewView = byId('review-view');
  const clipsRoot = byId('clips');
  const contextBar = byId('context-bar');
  const presetPicker = byId('preset-picker');
  const banner = byId('banner');
  const video = byId('video') as HTMLVideoElement;
  const timeline = byId('timeline');
  const coverage = byId('coverage');
  const findingList = byId('finding-list');
  const card = byId('finding');
  const title = byId('review-title');

  const dispatch = (action: Action): void => {
    state = reduce(state, action);
    render();
  };

  const fail = (err: unknown): void =>
    dispatch({ type: 'error', message: err instanceof Error ? err.message : String(err) });

  const renderList = (): void => {
    if (state.knowledge) {
      renderContextBar(contextBar, state.context, state.knowledge, (field, value) =>
        dispatch({ type: 'context_changed', field, value }),
      );
    }
    renderPresetPicker(presetPicker, state.preset, preset =>
      dispatch({ type: 'review_preset_chosen', preset: preset as Preset }),
    );
    renderHudHint(byId('hud-hint'), state.settings);
    renderClipList(clipsRoot, {
      clips: state.clips,
      jobs: state.jobs,
      options: state.reviewOptions,
      settings: state.settings,
      now: Date.now() / 1000,
      handlers: {
        onAnalyze: key => void analyze(key, false),
        onReanalyze: key => void analyze(key, true),
        onOpen: key => void open(key),
      },
    });
  };

  const renderReview = (): void => {
    const report = state.report;
    if (!report || !state.reviewKey) return;
    const marker = state.markers.find(m => m.id === state.selectedMarkerId) ?? null;
    const findingCount = state.markers.length;
    title.textContent = `${report.recording.name} — ${findingCount} finding${findingCount === 1 ? '' : 's'}`;
    renderCoverageSummary(coverage, report);
    renderTimeline(timeline, {
      markers: state.markers,
      coverage: state.coverage,
      durationS: report.recording.duration_s,
      selectedId: state.selectedMarkerId,
      onSelect: select,
      onSeek: seconds => {
        video.currentTime = seconds;
      },
    });
    renderFindingList(
      findingList,
      state.markers,
      state.selectedMarkerId,
      select,
      diagnosisOf(report),
    );
    const window_ = marker ? report.windows.find(w => w.index === marker.windowIndex) : undefined;
    renderFindingCard(card, {
      marker,
      frameUrl:
        marker?.finding.evidence_frame && state.reviewKey
          ? api.frameUrl(state.reviewKey, marker.finding.evidence_frame)
          : null,
      situationSummary: window_?.situation?.summary ?? null,
    });
  };

  const render = (): void => {
    banner.textContent = state.error ?? state.warning ?? '';
    banner.hidden = banner.textContent === '';
    banner.classList.toggle('error', state.error !== null);
    listView.hidden = state.view !== 'list';
    reviewView.hidden = state.view !== 'review';
    if (state.view === 'list') renderList();
    else renderReview();
  };

  const refreshClips = async (): Promise<void> => {
    try {
      const { clips, warning } = await api.getClips();
      dispatch({ type: 'clips_loaded', clips, warning });
    } catch (err) {
      fail(err);
    }
  };

  const analyze = async (key: string, force: boolean): Promise<void> => {
    const clip = state.clips.find(c => c.key === key);
    if (!clip) return;
    try {
      const job = await api.submitJob(clip.path, state.context, state.reviewOptions, force);
      dispatch({ type: 'job_submitted', job });
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
      const first = state.markers[0];
      if (first) video.currentTime = first.timestamp_s;
    } catch (err) {
      fail(err);
    }
  };

  const select = (id: string): void => {
    dispatch({ type: 'marker_selected', id });
    const marker = state.markers.find(m => m.id === id);
    if (marker) video.currentTime = marker.timestamp_s;
  };

  const step = (direction: 1 | -1): void => {
    dispatch({ type: 'marker_stepped', direction });
    const marker = state.markers.find(m => m.id === state.selectedMarkerId);
    if (marker) video.currentTime = marker.timestamp_s;
  };

  const pollJobs = async (): Promise<void> => {
    const finished: string[] = [];
    for (const id of state.activeJobIds) {
      try {
        const job = await api.getJob(id);
        if (job.status === 'done' || job.status === 'failed') finished.push(job.key);
        dispatch({ type: 'job_updated', job });
      } catch (err) {
        fail(err);
      }
    }
    // A finished job changes the ledger status, so refresh the list to pick up partial/done.
    if (finished.length > 0) await refreshClips();
  };

  byId('back').addEventListener('click', () => {
    video.pause();
    video.removeAttribute('src');
    video.load();
    dispatch({ type: 'back_to_list' });
    void refreshClips();
  });
  byId('refresh').addEventListener('click', () => void refreshClips());
  byId('prev-finding').addEventListener('click', () => step(-1));
  byId('next-finding').addEventListener('click', () => step(1));

  // Highlight the marker we are passing while the video plays.
  video.addEventListener('timeupdate', () => {
    const near = nearestMarker(
      state.markers,
      video.currentTime,
      MARKER_TOLERANCE_S,
      state.selectedMarkerId,
    );
    if (near && near.id !== state.selectedMarkerId)
      dispatch({ type: 'marker_selected', id: near.id });
  });
  video.addEventListener('error', () => {
    fail(
      'This clip cannot be played here (unsupported codec, likely HEVC). The findings below still apply.',
    );
  });

  document.addEventListener('keydown', event => {
    if (state.view !== 'review') return;
    if (event.key === 'ArrowRight' && event.shiftKey) step(1);
    else if (event.key === 'ArrowLeft' && event.shiftKey) step(-1);
    else if (event.key === ' ') {
      event.preventDefault();
      if (video.paused) void video.play();
      else video.pause();
    }
  });

  const loadReference = async (): Promise<void> => {
    try {
      const [knowledge, settings] = await Promise.all([api.getKnowledge(), api.getSettings()]);
      dispatch({ type: 'knowledge_loaded', knowledge });
      dispatch({ type: 'settings_loaded', settings });
    } catch (err) {
      fail(err);
    }
  };

  window.setInterval(() => void pollJobs(), POLL_MS);
  window.setInterval(() => {
    if (state.view === 'list' && state.activeJobIds.length === 0) void refreshClips();
  }, CLIP_REFRESH_MS);
  void loadReference();
  void refreshClips();
};

run(createApi(window.roundReview.apiBaseUrl, (url, init) => fetch(url, init)));
