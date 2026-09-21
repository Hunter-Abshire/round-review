// Mirrors the JSON shapes served by the Python API (src/round_review/server/app.py).
// Field names are snake_case because they are payload-shaped.

export const CLIP_STATUS = {
  new: 'new',
  queued: 'queued',
  running: 'running',
  done: 'done',
  partial: 'partial',
  failed: 'failed',
  skipped: 'skipped',
} as const;
export type ClipStatus = (typeof CLIP_STATUS)[keyof typeof CLIP_STATUS];

export interface Clip {
  name: string;
  path: string;
  key: string;
  size_bytes: number;
  mtime: number;
  duration_s: number | null;
  estimated_windows: number | null;
  status: ClipStatus;
  job_id: string | null;
  error: string | null;
}

export const JOB_STATUS = {
  queued: 'queued',
  running: 'running',
  done: 'done',
  failed: 'failed',
} as const;
export type JobStatus = (typeof JOB_STATUS)[keyof typeof JOB_STATUS];

export interface Job {
  id: string;
  path: string;
  key: string;
  status: JobStatus;
  windows_done: number;
  windows_total: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  context: PlayerContext;
  options: JobOptions;
}

export interface JobOptions {
  force: boolean;
  coverage: string | null;
  max_span_s: number | null;
  max_windows: number | null;
}

export const COVERAGE = { full: 'full', sampled: 'sampled' } as const;
export type CoverageMode = (typeof COVERAGE)[keyof typeof COVERAGE];

/** What a review will do, chosen in the app and sent with each job. */
export interface ReviewOptions {
  coverage: CoverageMode;
  max_span_s: number | null;
  max_windows: number | null;
}

export interface Settings {
  model: string;
  coverage: CoverageMode;
  coverage_modes: string[];
  window_s: number;
  windows_per_file: number;
  max_span_s: number;
  max_windows: number;
  fps: number;
  situation_pass: boolean;
  daily_call_cap: number;
}

export interface PlayerContext {
  rank: string | null;
  agent: string | null;
  map: string | null;
  side: string | null;
  focus: string | null;
  notes: string | null;
}

export const EMPTY_CONTEXT: PlayerContext = {
  rank: null,
  agent: null,
  map: null,
  side: null,
  focus: null,
  notes: null,
};

export interface KnowledgeCheck {
  id: string;
  check: string;
  fix: string;
}

export interface KnowledgeCategory {
  id: string;
  name: string;
  checks: KnowledgeCheck[];
}

export interface Knowledge {
  agents: Array<{ id: string; name: string; role: string }>;
  maps: Array<{ id: string; name: string }>;
  ranks: string[];
  checklist: KnowledgeCategory[];
}

export interface Situation {
  agent: string | null;
  map: string | null;
  side: string | null;
  phase: string | null;
  weapon: string | null;
  abilities_available: string[];
  credits: number | null;
  teammates_alive: number | null;
  enemies_visible: number;
  timeline: Array<{ t: number; event: string }>;
  summary: string;
}

export interface Finding {
  timestamp_s: number;
  check_id: string;
  check_label: string | null;
  category: string;
  observation: string;
  visible_evidence: string;
  information_available_to_player: string;
  information_revealed_later: string;
  assumption_flags: string[];
  suggested_alternative: string;
  confidence: number;
  evidence_frame: string | null;
}

export interface ReportWindow {
  index: number;
  start_s: number;
  end_s: number;
  model_calls: number;
  context: PlayerContext;
  situation: Situation | null;
  abstained_reason: string | null;
  findings: Finding[];
  warnings: string[];
}

export interface Report {
  recording: {
    name: string;
    path: string;
    duration_s: number;
    fps: number;
    width: number;
    height: number;
  };
  generated_at: string;
  model: string;
  partial: boolean;
  stopped_reason: string | null;
  windows: ReportWindow[];
  warnings: string[];
}
