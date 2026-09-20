// Mirrors the JSON shapes served by the Python API (src/round_review/server/app.py).
// Field names are snake_case because they are payload-shaped.

export const CLIP_STATUS = {
  new: 'new',
  queued: 'queued',
  running: 'running',
  done: 'done',
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
}

export interface Finding {
  timestamp_s: number;
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
  windows: ReportWindow[];
  warnings: string[];
}
