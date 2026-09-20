import type { Clip, Job, Report } from '../shared/types';
import { collectMarkers, type Marker } from './timeline';

export const VIEW = { list: 'list', review: 'review' } as const;
export type View = (typeof VIEW)[keyof typeof VIEW];

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
  selectedMarkerId: string | null;
}

export type Action =
  | { type: 'clips_loaded'; clips: Clip[]; warning: string | null }
  | { type: 'job_submitted'; job: Job }
  | { type: 'job_updated'; job: Job }
  | { type: 'report_loaded'; key: string; report: Report }
  | { type: 'marker_selected'; id: string }
  | { type: 'back_to_list' }
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
  selectedMarkerId: null,
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

export const reduce = (state: State, action: Action): State => {
  switch (action.type) {
    case 'clips_loaded':
      return { ...state, clips: action.clips, warning: action.warning, error: null };
    case 'job_submitted':
    case 'job_updated':
      return applyJob(state, action.job);
    case 'report_loaded':
      return {
        ...state,
        view: VIEW.review,
        reviewKey: action.key,
        report: action.report,
        markers: collectMarkers(action.report),
        selectedMarkerId: null,
        error: null,
      };
    case 'marker_selected':
      return {
        ...state,
        selectedMarkerId: state.markers.some(m => m.id === action.id) ? action.id : null,
      };
    case 'back_to_list':
      return {
        ...state,
        view: VIEW.list,
        reviewKey: null,
        report: null,
        markers: [],
        selectedMarkerId: null,
      };
    case 'error':
      return { ...state, error: action.message };
  }
};
