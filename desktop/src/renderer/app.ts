// Renderer entry point. Bundled by esbuild into dist/renderer/app.js; no Node access here.
import { createApi, type Api } from './api';
import {
  diagnosisOf,
  renderFindingDetail,
  renderGroupedFindings,
  renderSideTabs,
  renderAnswers,
  renderAskBox,
  renderClipList,
  renderCoachPanel,
  renderContextBar,
  renderCoverageSummary,
  renderHudHint,
  renderPresetPicker,
  renderSettings,
  renderTimeline,
} from './dom';
import {
  initialState,
  reduce,
  type Action,
  type Preset,
  type SidePanel,
  type State,
} from './state';
import { renderOverlay, videoContentRect } from './overlay';
import { nearestMarker } from './timeline';

declare global {
  interface Window {
    roundReview: { apiBaseUrl: string; onOpenSettings?: (callback: () => void) => void };
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
  const sideTabs = byId('side-tabs');
  const sideBody = byId('side-body');
  const overlay = byId('overlay') as unknown as SVGSVGElement;
  const settingsView = byId('settings-view');
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

  const drawOverlay = (): void => {
    const marker = state.markers.find(m => m.id === state.selectedMarkerId);
    const shapes = state.detailOpen && marker ? marker.finding.focus : [];
    const rect = videoContentRect(
      video.clientWidth,
      video.clientHeight,
      video.videoWidth,
      video.videoHeight,
    );
    overlay.style.width = `${video.clientWidth}px`;
    overlay.style.height = `${video.clientHeight}px`;
    renderOverlay(overlay, shapes, rect);
  };

  const renderSideBody = (): void => {
    const report = state.report;
    if (!report || !state.reviewKey) return;
    const marker = state.markers.find(m => m.id === state.selectedMarkerId) ?? null;

    if (state.sidePanel === 'ask') {
      sideBody.replaceChildren();
      const askPanel = document.createElement('div');
      const answersPanel = document.createElement('div');
      renderAskBox(askPanel, state.ask, {
        onAsk: question => void askCoach(question),
        onRangeChange: (start_s, end_s) => dispatch({ type: 'ask_range_changed', start_s, end_s }),
      });
      renderAnswers(
        answersPanel,
        state.answers,
        seconds => {
          video.currentTime = seconds;
        },
        state.ask.pendingQuestion,
      );
      sideBody.append(askPanel, answersPanel);
      return;
    }
    if (state.sidePanel === 'coach') {
      if (report.summary) {
        renderCoachPanel(sideBody, report.summary, seconds => {
          video.currentTime = seconds;
          void video.play();
        });
      } else {
        sideBody.replaceChildren();
      }
      return;
    }
    if (state.detailOpen && marker) {
      renderFindingDetail(
        sideBody,
        {
          marker,
          frameUrl: marker.finding.evidence_frame
            ? api.frameUrl(state.reviewKey, marker.finding.evidence_frame)
            : null,
          ordinalOf: marker.ordinal,
          total: state.markers.length,
          verdict: state.ratings[marker.id] ?? null,
        },
        {
          onBack: () => dispatch({ type: 'detail_closed' }),
          onStep: direction => step(direction),
          onRate: verdict => {
            dispatch({ type: 'finding_rated', id: marker.id, verdict });
            if (state.reviewKey) {
              // Fire and forget: a lost opinion must never interrupt what is being read.
              void api
                .rateFinding(state.reviewKey, marker.finding.check_id, marker.timestamp_s, verdict)
                .catch(() => undefined);
            }
          },
        },
      );
      return;
    }
    renderGroupedFindings(
      sideBody,
      state.markers,
      state.selectedMarkerId,
      id => open_finding(id),
      diagnosisOf(report),
    );
  };

  const renderReview = (): void => {
    const report = state.report;
    if (!report || !state.reviewKey) return;
    const findingCount = state.markers.length;
    title.textContent = `${report.recording.name} — ${findingCount} finding${findingCount === 1 ? '' : 's'}`;
    renderCoverageSummary(coverage, report);
    renderTimeline(timeline, {
      markers: state.markers,
      coverage: state.coverage,
      durationS: report.recording.duration_s,
      selectedId: state.selectedMarkerId,
      onSelect: open_finding,
      onSeek: seconds => {
        video.currentTime = seconds;
      },
    });
    renderSideTabs(sideTabs, state.sidePanel, findingCount, panel =>
      dispatch({
        type: 'side_panel_picked',
        panel: panel as SidePanel,
        timestamp_s: video.currentTime,
      }),
    );
    renderSideBody();
    drawOverlay();
  };

  const renderSettingsView = (): void => {
    renderSettings(
      settingsView,
      {
        document: state.config ?? undefined,
        edits: state.configEdits,
        saving: state.configSaving,
        showAdvanced: state.showAdvanced,
      },
      {
        onChange: (name, value) => dispatch({ type: 'config_edited', name, value }),
        onSave: () => void saveConfig(),
        onClose: () => dispatch({ type: 'settings_closed' }),
        onToggleAdvanced: () => dispatch({ type: 'advanced_toggled' }),
      },
    );
  };

  const openSettings = async (): Promise<void> => {
    dispatch({ type: 'settings_opened' });
    try {
      dispatch({ type: 'config_loaded', config: await api.getConfig() });
    } catch (err) {
      fail(err);
    }
  };

  const saveConfig = async (): Promise<void> => {
    dispatch({ type: 'config_saving' });
    try {
      await api.saveConfig(state.configEdits);
      const [config, settings] = await Promise.all([api.getConfig(), api.getSettings()]);
      dispatch({ type: 'config_saved', config });
      dispatch({ type: 'settings_loaded', settings });
      void refreshClips();
    } catch (err) {
      dispatch({ type: 'config_save_failed' });
      fail(err);
    }
  };

  const render = (): void => {
    banner.textContent = state.error ?? state.warning ?? '';
    banner.hidden = banner.textContent === '';
    banner.classList.toggle('error', state.error !== null);
    listView.hidden = state.view !== 'list';
    reviewView.hidden = state.view !== 'review';
    settingsView.hidden = state.view !== 'settings';
    if (state.view === 'settings') renderSettingsView();
    else if (state.view === 'list') renderList();
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

  const askCoach = async (question: string): Promise<void> => {
    const clip = state.clips.find(c => c.key === state.reviewKey);
    if (!clip) return;
    try {
      const job = await api.ask(
        clip.path,
        state.ask.start_s,
        state.ask.end_s,
        question,
        state.context,
      );
      dispatch({ type: 'ask_submitted', jobId: job.id, question });
    } catch (err) {
      dispatch({ type: 'ask_failed' });
      fail(err);
    }
  };

  const pollAsk = async (): Promise<void> => {
    if (state.askJobId === null) return;
    try {
      const job = await api.getJob(state.askJobId);
      if (job.status === 'done' && job.answer) {
        dispatch({ type: 'answer_received', answer: job.answer });
      } else if (job.status === 'failed') {
        dispatch({ type: 'ask_failed' });
        fail(new Error(job.error ?? 'the question could not be answered'));
      }
    } catch (err) {
      dispatch({ type: 'ask_failed' });
      fail(err);
    }
  };

  /** Opening a finding seeks to it and shows it in the sidebar. */
  const open_finding = (id: string): void => {
    dispatch({ type: 'marker_opened', id });
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
  byId('settings-button').addEventListener('click', () => void openSettings());
  window.roundReview.onOpenSettings?.(() => void openSettings());
  byId('prev-finding').addEventListener('click', () => step(-1));
  byId('next-finding').addEventListener('click', () => step(1));

  // Highlight the marker we are passing while the video plays.
  // Right-clicking the timeline asks about that moment.
  timeline.addEventListener('contextmenu', event => {
    event.preventDefault();
    const bounds = timeline.getBoundingClientRect();
    const duration = state.report?.recording.duration_s ?? 0;
    if (bounds.width > 0 && duration > 0) {
      const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
      dispatch({ type: 'ask_around', timestamp_s: ratio * duration });
    }
  });
  byId('ask-here').addEventListener('click', () => {
    dispatch({ type: 'ask_around', timestamp_s: video.currentTime });
    dispatch({ type: 'side_panel_picked', panel: 'ask', timestamp_s: video.currentTime });
  });

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
  video.addEventListener('loadedmetadata', () => drawOverlay());
  window.addEventListener('resize', () => drawOverlay());
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
  window.setInterval(() => void pollAsk(), POLL_MS);
  window.setInterval(() => {
    if (state.view === 'list' && state.activeJobIds.length === 0) void refreshClips();
  }, CLIP_REFRESH_MS);
  void loadReference();
  void refreshClips();
};

run(createApi(window.roundReview.apiBaseUrl, (url, init) => fetch(url, init)));
