import type { Clip, Finding, Job, Report } from '../../src/shared/types';

export const finding = (overrides: Partial<Finding> = {}): Finding => ({
  timestamp_s: 64.4,
  category: 'positioning',
  observation: 'Held a wide angle.',
  visible_evidence: 'Minimap shows no teammate right.',
  information_available_to_player: 'Minimap, timer 1:05.',
  information_revealed_later: '',
  assumption_flags: ['enemy position'],
  suggested_alternative: 'Tighten the angle.',
  confidence: 0.8,
  evidence_frame: 'frames/w00_004.jpg',
  ...overrides,
});

export const report = (overrides: Partial<Report> = {}): Report => ({
  recording: {
    name: 'match.mp4',
    path: '/v/match.mp4',
    duration_s: 600,
    fps: 60,
    width: 1920,
    height: 1080,
  },
  generated_at: '2026-09-20T15:30:00+00:00',
  model: 'qwen3-vl:8b',
  windows: [
    { index: 0, start_s: 60, end_s: 72, model_calls: 1, findings: [finding()], warnings: [] },
    {
      index: 1,
      start_s: 300,
      end_s: 312,
      model_calls: 1,
      findings: [finding({ timestamp_s: 305, category: 'utility' }), finding({ timestamp_s: 310 })],
      warnings: ['window warning'],
    },
  ],
  warnings: [],
  ...overrides,
});

export const clip = (overrides: Partial<Clip> = {}): Clip => ({
  name: 'match.mp4',
  path: '/v/match.mp4',
  key: '0123456789abcdef',
  size_bytes: 1000,
  mtime: 0,
  status: 'new',
  job_id: null,
  error: null,
  ...overrides,
});

export const job = (overrides: Partial<Job> = {}): Job => ({
  id: 'job1',
  path: '/v/match.mp4',
  key: '0123456789abcdef',
  status: 'queued',
  windows_done: 0,
  windows_total: 0,
  error: null,
  created_at: '2026-09-20T12:00:00+00:00',
  finished_at: null,
  ...overrides,
});
