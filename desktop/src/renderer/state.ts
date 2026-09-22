import {
  EMPTY_CONTEXT,
  type Clip,
  type Job,
  type Knowledge,
  type PlayerContext,
  type Answer,
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

/** What the player is about to ask about, and whether an answer is on its way. */
export interface AskState {
  start_s: number;
  end_s: number;
  pending: boolean;
  maxSpanS: number;
}

/** Offered as one-click chips, because most questions about a moment are one of these. */
export const SUGGESTED_QUESTIONS: readonly string[] = [
  'What should I have done instead?',
  'How could I have used my utility here?',
  'Was this peek reasonable?',
  'Where should I have been standing?',
  'What did I miss on the minimap?',
];

// A bare click means "around here": this much footage either side of the playhead.
const CLICK_HALF_SPAN_S = 6;

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
  ask: AskState;
  answers: Answer[];
  askJobId: string | null;
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
  | { type: 'ask_range_changed'; start_s: number; end_s: number }
  | { type: 'ask_around'; timestamp_s: number }
  | { type: 'ask_submitted'; jobId: string }
  | { type: 'answer_received'; answer: Answer }
  | { type: 'ask_failed' }
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
  ask: { start_s: 0, end_s: 0, pending: false, maxSpanS: 60 },
  answers: [],
  askJobId: null,
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
        answers: [],
        askJobId: null,
        ask: { ...state.ask, pending: false },
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
        ask: { ...state.ask, maxSpanS: action.settings.max_question_span_s || 60 },
      };
    }
    case 'review_preset_chosen':
      return { ...state, preset: action.preset, reviewOptions: PRESET_OPTIONS[action.preset] };
    case 'ask_range_changed':
      return { ...state, ask: { ...state.ask, start_s: action.start_s, end_s: action.end_s } };
    case 'ask_around':
      return {
        ...state,
        ask: {
          ...state.ask,
          start_s: Math.max(0, action.timestamp_s - CLICK_HALF_SPAN_S),
          end_s: action.timestamp_s + CLICK_HALF_SPAN_S,
        },
      };
    case 'ask_submitted':
      return { ...state, askJobId: action.jobId, ask: { ...state.ask, pending: true } };
    case 'answer_received':
      return {
        ...state,
        answers: [...state.answers, action.answer],
        askJobId: null,
        ask: { ...state.ask, pending: false },
      };
    case 'ask_failed':
      return { ...state, askJobId: null, ask: { ...state.ask, pending: false } };
    case 'error':
      return { ...state, error: action.message };
  }
};
