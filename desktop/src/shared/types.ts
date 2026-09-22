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
  estimated_seconds: number | null;
  estimated_time: string | null;
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
  kind: 'review' | 'question';
  question: { start_s: number; end_s: number; question: string } | null;
  answer: Answer | null;
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
  question_frames: number;
  max_question_span_s: number;
  hud_check: boolean;
  hud_ready: boolean;
  hud_missing_characters: string[];
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

export interface StrengthItem {
  timestamp_s: number;
  check_id: string;
  check_label: string | null;
  category: string;
  observation: string;
  visible_evidence: string;
  why_it_worked: string;
  confidence: number;
  evidence_frame: string | null;
}

/** One habit: a checklist item and every time it came up in this review. */
export interface Habit {
  check_id: string;
  check_label: string | null;
  category: string;
  count: number;
  windows: number[];
  score: number;
  mean_confidence: number;
  timestamps: number[];
  observation: string;
  visible_evidence: string;
  information_revealed_later: string;
  assumption_flags: string[];
  suggested_alternative: string;
  evidence_frame: string | null;
}

export interface PracticeItem {
  for_check_id: string;
  rule: string;
  drill: string;
  success_check: string;
}

export interface ReportSummary {
  verdict: string;
  rank_focus: string | null;
  windows_reviewed: number;
  focus: Habit[];
  hindsight: Habit[];
  also_seen: Array<{
    check_id: string;
    check_label: string | null;
    category: string;
    count: number;
  }>;
  strengths: StrengthItem[];
  practice: PracticeItem | null;
}

export interface Alternative {
  action: string;
  why: string;
}

export interface Answer {
  question: string;
  start_s: number;
  end_s: number;
  answerable: boolean;
  answer: string;
  what_you_could_see: string;
  what_you_could_not_know: string;
  assumptions: string[];
  alternatives: Alternative[];
  confidence: number;
  warnings: string[];
  sources: string[];
}

export interface ReportWindow {
  index: number;
  start_s: number;
  end_s: number;
  model_calls: number;
  context: PlayerContext;
  situation: Situation | null;
  abstained_reason: string | null;
  strengths: StrengthItem[];
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
  summary: ReportSummary | null;
  windows: ReportWindow[];
  warnings: string[];
}

export const FIELD_KIND = {
  bool: 'bool',
  int: 'int',
  float: 'float',
  text: 'text',
  path: 'path',
  choice: 'choice',
} as const;
export type FieldKind = (typeof FIELD_KIND)[keyof typeof FIELD_KIND];

/** One setting, described well enough to render without knowing what it means. */
export interface ConfigField {
  name: string;
  group: string;
  label: string;
  help: string;
  kind: FieldKind;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  unit: string;
  advanced: boolean;
  value: string | number | boolean | null;
  default: string | number | boolean | null;
  overridden_by_env: string | null;
}

export interface ConfigDocument {
  path: string;
  groups: string[];
  fields: ConfigField[];
}
