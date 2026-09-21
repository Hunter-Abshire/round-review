import {
  EMPTY_CONTEXT,
  type Clip,
  type Job,
  type Knowledge,
  type PlayerContext,
  type Report,
  type ReviewOptions,
  type Settings,
} from '../shared/types';
import {
  collectMarkers,
  coverageBands,
  nextMarker,
  type CoverageBand,
  type Marker,
} from './timeline';

export const VIEW = { list: 'list', review: 'review' } as const;
export type View = (typeof VIEW)[keyof typeof VIEW];

export const PRESET = { full: 'full', first_minute: 'first_minute', sampled: 'sampled' } as const;
export type Preset = (typeof PRESET)[keyof typeof PRESET];

const PRESET_OPTIONS: Record<Preset, ReviewOptions> = {
  full: { coverage: 'full', max_span_s: null, max_windows: null },
  first_minute: { coverage: 'full', max_span_s: 60, max_windows: null },
  sampled: { coverage: 'sampled', max_span_s: null, max_windows: null },
};

export interface State {
  view: View;
  clips: Clip[];
  jobs: Record<string, Job>;
  activeJobIds: string[];
  warning: string | null;
  error: string | null;
  reviewKey: string | null;
  report: Report | null;
  markers: Marker[];
  coverage: CoverageBand[];
  selectedMarkerId: string | null;
  context: PlayerContext;
  knowledge: Knowledge | null;
  settings: Settings | null;
  reviewOptions: ReviewOptions;
  preset: Preset;
}

export type ContextField = keyof PlayerContext;

export type Action =
  | { type: 'clips_loaded'; clips: Clip[]; warning: string | null }
  | { type: 'job_submitted'; job: Job }
  | { type: 'job_updated'; job: Job }
  | { type: 'report_loaded'; key: string; report: Report }
  | { type: 'marker_selected'; id: string }
  | { type: 'marker_stepped'; direction: 1 | -1 }
  | { type: 'back_to_list' }
  | { type: 'context_changed'; field: ContextField; value: string }
  | { type: 'knowledge_loaded'; knowledge: Knowledge }
  | { type: 'settings_loaded'; settings: Settings }
  | { type: 'review_preset_chosen'; preset: Preset }
  | { type: 'error'; message: string };

export const initialState: State = {
  view: VIEW.list,
  clips: [],
  jobs: {},
  activeJobIds: [],
  warning: null,
  error: null,
  reviewKey: null,
  report: null,
  markers: [],
  coverage: [],
  selectedMarkerId: null,
  context: EMPTY_CONTEXT,
  knowledge: null,
  settings: null,
  reviewOptions: PRESET_OPTIONS.full,
  preset: PRESET.full,
};

const ACTIVE_JOB = new Set<string>(['queued', 'running']);

const applyJob = (state: State, job: Job): State => {
  const jobs = { ...state.jobs, [job.id]: job };
  const clips = state.clips.map(c =>
    c.key === job.key ? { ...c, status: job.status, job_id: job.id, error: job.error } : c,
  );
  const activeJobIds = Object.values(jobs)
    .filter(j => ACTIVE_JOB.has(j.status))
    .map(j => j.id);
  return { ...state, jobs, clips, activeJobIds };
};

/** Settings describe the effective config, so the app opens showing what a review will do. */
const optionsFromSettings = (settings: Settings): ReviewOptions => ({
  coverage: settings.coverage,
  max_span_s: settings.max_span_s || null,
  max_windows: settings.max_windows || null,
});

const presetFor = (options: ReviewOptions): Preset => {
  if (options.coverage === 'sampled') return PRESET.sampled;
  return options.max_span_s ? PRESET.first_minute : PRESET.full;
};

export const reduce = (state: State, action: Action): State => {
  switch (action.type) {
    case 'clips_loaded':
      return { ...state, clips: action.clips, warning: action.warning, error: null };
    case 'job_submitted':
    case 'job_updated':
      return applyJob(state, action.job);
    case 'report_loaded': {
      const markers = collectMarkers(action.report);
      return {
        ...state,
        view: VIEW.review,
        reviewKey: action.key,
        report: action.report,
        markers,
        coverage: coverageBands(action.report),
        selectedMarkerId: markers[0]?.id ?? null,
        error: null,
      };
    }
    case 'marker_selected':
      return state.markers.some(m => m.id === action.id)
        ? { ...state, selectedMarkerId: action.id }
        : state;
    case 'marker_stepped': {
      const marker = nextMarker(state.markers, state.selectedMarkerId, action.direction);
      return marker ? { ...state, selectedMarkerId: marker.id } : state;
    }
    case 'back_to_list':
      return {
        ...state,
        view: VIEW.list,
        reviewKey: null,
        report: null,
        markers: [],
        coverage: [],
        selectedMarkerId: null,
      };
    case 'context_changed':
      return {
        ...state,
        context: {
          ...state.context,
          [action.field]: action.value.trim() === '' ? null : action.value.trim(),
        },
      };
    case 'knowledge_loaded':
      return { ...state, knowledge: action.knowledge };
    case 'settings_loaded': {
      const reviewOptions = optionsFromSettings(action.settings);
      return {
        ...state,
        settings: action.settings,
        reviewOptions,
        preset: presetFor(reviewOptions),
      };
    }
    case 'review_preset_chosen':
      return { ...state, preset: action.preset, reviewOptions: PRESET_OPTIONS[action.preset] };
    case 'error':
      return { ...state, error: action.message };
  }
};
