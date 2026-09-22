import {
  EMPTY_CONTEXT,
  type Clip,
  type Job,
  type Knowledge,
  type PlayerContext,
  type Answer,
  type ConfigDocument,
  type Report,
  type ReviewOptions,
  type Settings,
  type Verdict,
} from '../shared/types';
import {
  collectMarkers,
  coverageBands,
  nextMarker,
  type CoverageBand,
  type Marker,
} from './timeline';

export const VIEW = { list: 'list', review: 'review', settings: 'settings' } as const;
export type View = (typeof VIEW)[keyof typeof VIEW];

export const SIDE_PANEL = { findings: 'findings', coach: 'coach', ask: 'ask' } as const;
export type SidePanel = (typeof SIDE_PANEL)[keyof typeof SIDE_PANEL];

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
  // The question being answered, so the panel can show it working rather than looking dead.
  pendingQuestion: string | null;
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
  config: ConfigDocument | null;
  configEdits: Record<string, unknown>;
  configSaving: boolean;
  showAdvanced: boolean;
  // The view to come back to when settings close.
  previousView: View;
  sidePanel: SidePanel;
  // Whether the sidebar is showing one finding rather than the list. Following the video
  // highlights a finding without opening it, so playback never hijacks what you are reading.
  detailOpen: boolean;
  // What the player said about each finding, keyed by marker id. Local to this report;
  // the server keeps the durable record.
  ratings: Record<string, Verdict>;
}

export type ContextField = keyof PlayerContext;

export type Action =
  | { type: 'clips_loaded'; clips: Clip[]; warning: string | null }
  | { type: 'job_submitted'; job: Job }
  | { type: 'job_updated'; job: Job }
  | { type: 'report_loaded'; key: string; report: Report }
  | { type: 'marker_selected'; id: string }
  | { type: 'marker_opened'; id: string }
  | { type: 'detail_closed' }
  | { type: 'finding_rated'; id: string; verdict: Verdict }
  | { type: 'side_panel_picked'; panel: SidePanel; timestamp_s?: number }
  | { type: 'marker_stepped'; direction: 1 | -1 }
  | { type: 'back_to_list' }
  | { type: 'context_changed'; field: ContextField; value: string }
  | { type: 'knowledge_loaded'; knowledge: Knowledge }
  | { type: 'settings_loaded'; settings: Settings }
  | { type: 'review_preset_chosen'; preset: Preset }
  | { type: 'ask_range_changed'; start_s: number; end_s: number }
  | { type: 'ask_around'; timestamp_s: number }
  | { type: 'ask_submitted'; jobId: string; question: string }
  | { type: 'answer_received'; answer: Answer }
  | { type: 'ask_failed' }
  | { type: 'settings_opened' }
  | { type: 'config_loaded'; config: ConfigDocument }
  | { type: 'config_edited'; name: string; value: unknown }
  | { type: 'config_saving' }
  | { type: 'config_saved'; config: ConfigDocument }
  | { type: 'config_save_failed' }
  | { type: 'advanced_toggled' }
  | { type: 'settings_closed' }
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
  ask: { start_s: 0, end_s: 0, pending: false, maxSpanS: 60, pendingQuestion: null },
  answers: [],
  askJobId: null,
  config: null,
  configEdits: {},
  configSaving: false,
  showAdvanced: false,
  previousView: VIEW.list,
  sidePanel: SIDE_PANEL.findings,
  detailOpen: false,
  ratings: {},
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
        sidePanel: SIDE_PANEL.findings,
        detailOpen: false,
        // Marker ids are per report, so opinions from the last one must not carry over.
        ratings: {},
        error: null,
      };
    }
    case 'marker_selected':
      return state.markers.some(m => m.id === action.id)
        ? { ...state, selectedMarkerId: action.id }
        : state;
    case 'marker_opened':
      return state.markers.some(m => m.id === action.id)
        ? {
            ...state,
            selectedMarkerId: action.id,
            detailOpen: true,
            sidePanel: SIDE_PANEL.findings,
          }
        : state;
    case 'finding_rated':
      return { ...state, ratings: { ...state.ratings, [action.id]: action.verdict } };
    case 'detail_closed':
      return { ...state, detailOpen: false };
    case 'side_panel_picked': {
      // Opening Ask points it at whatever you are watching, rather than at 0:00.
      const ask =
        action.panel === SIDE_PANEL.ask && action.timestamp_s !== undefined
          ? {
              ...state.ask,
              start_s: Math.max(0, action.timestamp_s - CLICK_HALF_SPAN_S),
              end_s: action.timestamp_s + CLICK_HALF_SPAN_S,
            }
          : state.ask;
      return { ...state, sidePanel: action.panel, detailOpen: false, ask };
    }
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
        ask: { ...state.ask, pending: false, pendingQuestion: null },
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
      return {
        ...state,
        askJobId: action.jobId,
        ask: { ...state.ask, pending: true, pendingQuestion: action.question },
      };
    case 'answer_received':
      return {
        ...state,
        answers: [...state.answers, action.answer],
        askJobId: null,
        ask: { ...state.ask, pending: false, pendingQuestion: null },
      };
    case 'ask_failed':
      return {
        ...state,
        askJobId: null,
        ask: { ...state.ask, pending: false, pendingQuestion: null },
      };
    case 'settings_opened':
      return {
        ...state,
        previousView: state.view === VIEW.settings ? state.previousView : state.view,
        view: VIEW.settings,
      };
    case 'config_loaded':
      return { ...state, config: action.config };
    case 'config_edited': {
      const field = state.config?.fields.find(f => f.name === action.name);
      const edits = { ...state.configEdits };
      // Setting a value back to what is saved is not an edit.
      if (field && field.value === action.value) delete edits[action.name];
      else edits[action.name] = action.value;
      return { ...state, configEdits: edits };
    }
    case 'config_saving':
      return { ...state, configSaving: true };
    case 'config_saved':
      return { ...state, config: action.config, configEdits: {}, configSaving: false };
    case 'config_save_failed':
      return { ...state, configSaving: false };
    case 'advanced_toggled':
      return { ...state, showAdvanced: !state.showAdvanced };
    case 'settings_closed':
      return { ...state, view: state.previousView, configEdits: {} };
    case 'error':
      return { ...state, error: action.message };
  }
};
