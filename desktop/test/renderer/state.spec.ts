import { initialState, reduce, type State } from '../../src/renderer/state';
import { EMPTY_CONTEXT } from '../../src/shared/types';
import { answer as answerFixture, clip, configDocument, job, report, settings } from './fixtures';

const withClips = (): State =>
  reduce(initialState, {
    type: 'clips_loaded',
    clips: [clip(), clip({ name: 'b.mp4', key: 'ffffffffffffffff' })],
    warning: null,
  });

describe('clips and jobs', () => {
  it('loads clips and clears any previous error', () => {
    const s = reduce(
      { ...initialState, error: 'old' },
      { type: 'clips_loaded', clips: [clip()], warning: 'w' },
    );
    expect(s.clips).toHaveLength(1);
    expect(s.warning).toBe('w');
    expect(s.error).toBeNull();
  });

  it('marks a clip queued and tracks the job when analysis is requested', () => {
    const s = reduce(withClips(), { type: 'job_submitted', job: job() });
    expect(s.clips[0]?.status).toBe('queued');
    expect(s.clips[0]?.job_id).toBe('job1');
    expect(s.jobs['job1']).toEqual(job());
    expect(s.clips[1]?.status).toBe('new');
  });

  it('updates clip status and progress from job updates', () => {
    let s = reduce(withClips(), { type: 'job_submitted', job: job() });
    s = reduce(s, {
      type: 'job_updated',
      job: job({ status: 'running', windows_done: 1, windows_total: 47 }),
    });
    expect(s.clips[0]?.status).toBe('running');
    expect(s.jobs['job1']?.windows_total).toBe(47);
    s = reduce(s, {
      type: 'job_updated',
      job: job({ status: 'failed', error: 'OllamaError: refused' }),
    });
    expect(s.clips[0]?.status).toBe('failed');
    expect(s.clips[0]?.error).toBe('OllamaError: refused');
  });

  it('lists ids of jobs that still need polling', () => {
    let s = reduce(withClips(), { type: 'job_submitted', job: job() });
    s = reduce(s, {
      type: 'job_submitted',
      job: job({ id: 'job2', key: 'ffffffffffffffff', status: 'running' }),
    });
    s = reduce(s, { type: 'job_updated', job: job({ status: 'done' }) });
    expect(s.activeJobIds).toEqual(['job2']);
  });

  it('records errors without losing clips', () => {
    const s = reduce(withClips(), { type: 'error', message: 'boom' });
    expect(s.error).toBe('boom');
    expect(s.clips).toHaveLength(2);
  });
});

describe('review view', () => {
  it('opens a report with markers and coverage', () => {
    let s = reduce(withClips(), {
      type: 'report_loaded',
      key: '0123456789abcdef',
      report: report(),
    });
    expect(s.view).toBe('review');
    expect(s.reviewKey).toBe('0123456789abcdef');
    expect(s.markers).toHaveLength(3);
    expect(s.coverage).toHaveLength(2);
    expect(s.selectedMarkerId).toBe('w0-f0'); // first finding selected so the panel is never blank
    s = reduce(s, { type: 'marker_selected', id: 'w1-f0' });
    expect(s.selectedMarkerId).toBe('w1-f0');
    s = reduce(s, { type: 'marker_selected', id: 'nope' });
    expect(s.selectedMarkerId).toBe('w1-f0'); // unknown ids leave the selection alone
    s = reduce(s, { type: 'back_to_list' });
    expect(s.view).toBe('list');
    expect(s.report).toBeNull();
    expect(s.markers).toEqual([]);
  });

  it('selects nothing when a review found nothing', () => {
    const s = reduce(initialState, {
      type: 'report_loaded',
      key: 'k',
      report: report({ windows: [] }),
    });
    expect(s.selectedMarkerId).toBeNull();
    expect(s.markers).toEqual([]);
  });

  it('steps between findings', () => {
    let s = reduce(initialState, { type: 'report_loaded', key: 'k', report: report() });
    s = reduce(s, { type: 'marker_stepped', direction: 1 });
    expect(s.selectedMarkerId).toBe('w1-f0');
    s = reduce(s, { type: 'marker_stepped', direction: -1 });
    expect(s.selectedMarkerId).toBe('w0-f0');
  });
});

describe('context, settings and review options', () => {
  it('updates context one field at a time, blank meaning null', () => {
    let s = reduce(initialState, { type: 'context_changed', field: 'rank', value: 'Gold 2' });
    expect(s.context).toEqual({ ...EMPTY_CONTEXT, rank: 'Gold 2' });
    s = reduce(s, { type: 'context_changed', field: 'agent', value: 'Jett' });
    s = reduce(s, { type: 'context_changed', field: 'rank', value: '' });
    expect(s.context).toEqual({ ...EMPTY_CONTEXT, agent: 'Jett' });
  });

  it('stores knowledge', () => {
    const knowledge = {
      agents: [{ id: 'jett', name: 'Jett', role: 'duelist' }],
      maps: [],
      ranks: ['Gold'],
      checklist: [],
    };
    expect(reduce(initialState, { type: 'knowledge_loaded', knowledge }).knowledge).toBe(knowledge);
  });

  it('adopts the review defaults from settings', () => {
    const s = reduce(initialState, {
      type: 'settings_loaded',
      settings: settings({ coverage: 'sampled', max_span_s: 60, max_windows: 5 }),
    });
    expect(s.settings?.model).toBe('qwen3-vl:8b');
    expect(s.reviewOptions).toEqual({ coverage: 'sampled', max_span_s: 60, max_windows: 5 });
  });

  it('defaults to reviewing the whole clip', () => {
    expect(initialState.reviewOptions).toEqual({
      coverage: 'full',
      max_span_s: null,
      max_windows: null,
    });
  });

  it('switches between whole-clip and first-minute presets', () => {
    let s = reduce(initialState, { type: 'review_preset_chosen', preset: 'first_minute' });
    expect(s.reviewOptions).toEqual({ coverage: 'full', max_span_s: 60, max_windows: null });
    s = reduce(s, { type: 'review_preset_chosen', preset: 'sampled' });
    expect(s.reviewOptions.coverage).toBe('sampled');
    s = reduce(s, { type: 'review_preset_chosen', preset: 'full' });
    expect(s.reviewOptions).toEqual({ coverage: 'full', max_span_s: null, max_windows: null });
  });

  it('remembers which preset is active', () => {
    expect(
      reduce(initialState, { type: 'review_preset_chosen', preset: 'first_minute' }).preset,
    ).toBe('first_minute');
    expect(initialState.preset).toBe('full');
  });
});

describe('asking about a moment', () => {
  it('starts with no answers and nothing pending', () => {
    expect(initialState.answers).toEqual([]);
    expect(initialState.ask.pending).toBe(false);
    expect(initialState.askJobId).toBeNull();
  });

  it('takes the question span from settings', () => {
    const s = reduce(initialState, {
      type: 'settings_loaded',
      settings: settings({ max_question_span_s: 30 }),
    });
    expect(s.ask.maxSpanS).toBe(30);
  });

  it('sets a range around a clicked moment', () => {
    const s = reduce(initialState, { type: 'ask_around', timestamp_s: 100 });
    expect(s.ask.start_s).toBe(94);
    expect(s.ask.end_s).toBe(106);
  });

  it('never starts the range before the recording', () => {
    expect(reduce(initialState, { type: 'ask_around', timestamp_s: 2 }).ask.start_s).toBe(0);
  });

  it('tracks a pending question and collects the answer', () => {
    let s = reduce(initialState, { type: 'ask_submitted', jobId: 'j9', question: 'why?' });
    expect(s.ask.pending).toBe(true);
    expect(s.askJobId).toBe('j9');
    s = reduce(s, { type: 'answer_received', answer: answerFixture() });
    expect(s.ask.pending).toBe(false);
    expect(s.askJobId).toBeNull();
    expect(s.answers).toHaveLength(1);
  });

  it('clears pending when the question fails', () => {
    let s = reduce(initialState, { type: 'ask_submitted', jobId: 'j9', question: 'why?' });
    s = reduce(s, { type: 'ask_failed' });
    expect(s.ask.pending).toBe(false);
    expect(s.askJobId).toBeNull();
  });

  it('forgets the answers when you go back to the clip list', () => {
    let s = reduce(initialState, { type: 'answer_received', answer: answerFixture() });
    s = reduce(s, { type: 'back_to_list' });
    expect(s.answers).toEqual([]);
  });
});

describe('settings', () => {
  it('opens over whatever you were looking at and comes back to it', () => {
    let s = reduce(initialState, { type: 'report_loaded', key: 'k', report: report() });
    expect(s.view).toBe('review');
    s = reduce(s, { type: 'settings_opened' });
    expect(s.view).toBe('settings');
    s = reduce(s, { type: 'settings_closed' });
    expect(s.view).toBe('review');
  });

  it('opening settings twice still returns to the original view', () => {
    let s = reduce(initialState, { type: 'settings_opened' });
    s = reduce(s, { type: 'settings_opened' });
    expect(reduce(s, { type: 'settings_closed' }).view).toBe('list');
  });

  it('tracks edits and forgets them when closed without saving', () => {
    let s = reduce(initialState, { type: 'config_loaded', config: configDocument() });
    s = reduce(s, { type: 'config_edited', name: 'coverage', value: 'sampled' });
    expect(s.configEdits).toEqual({ coverage: 'sampled' });
    s = reduce(s, { type: 'settings_closed' });
    expect(s.configEdits).toEqual({});
  });

  it('setting a value back to what is saved is not an edit', () => {
    let s = reduce(initialState, { type: 'config_loaded', config: configDocument() });
    s = reduce(s, { type: 'config_edited', name: 'coverage', value: 'sampled' });
    s = reduce(s, { type: 'config_edited', name: 'coverage', value: 'full' });
    expect(s.configEdits).toEqual({});
  });

  it('clears the edits once they are saved', () => {
    let s = reduce(initialState, { type: 'config_loaded', config: configDocument() });
    s = reduce(s, { type: 'config_edited', name: 'coverage', value: 'sampled' });
    s = reduce(s, { type: 'config_saving' });
    expect(s.configSaving).toBe(true);
    s = reduce(s, { type: 'config_saved', config: configDocument() });
    expect(s.configEdits).toEqual({});
    expect(s.configSaving).toBe(false);
  });

  it('keeps the edits when saving fails, so nothing is lost', () => {
    let s = reduce(initialState, { type: 'config_loaded', config: configDocument() });
    s = reduce(s, { type: 'config_edited', name: 'coverage', value: 'sampled' });
    s = reduce(s, { type: 'config_saving' });
    s = reduce(s, { type: 'config_save_failed' });
    expect(s.configSaving).toBe(false);
    expect(s.configEdits).toEqual({ coverage: 'sampled' });
  });

  it('toggles advanced settings', () => {
    expect(reduce(initialState, { type: 'advanced_toggled' }).showAdvanced).toBe(true);
  });
});

describe('review sidebar', () => {
  it('starts on the findings list with nothing open', () => {
    expect(initialState.sidePanel).toBe('findings');
    expect(initialState.detailOpen).toBe(false);
  });

  it('opens the detail when a finding is picked and closes on back', () => {
    let s = reduce(initialState, { type: 'report_loaded', key: 'k', report: report() });
    s = reduce(s, { type: 'marker_opened', id: 'w1-f0' });
    expect(s.detailOpen).toBe(true);
    expect(s.selectedMarkerId).toBe('w1-f0');
    expect(s.sidePanel).toBe('findings');
    s = reduce(s, { type: 'detail_closed' });
    expect(s.detailOpen).toBe(false);
  });

  it('following the video highlights without opening the detail', () => {
    let s = reduce(initialState, { type: 'report_loaded', key: 'k', report: report() });
    s = reduce(s, { type: 'marker_selected', id: 'w1-f0' });
    expect(s.detailOpen).toBe(false);
  });

  it('switching to ask leaves the detail closed', () => {
    let s = reduce(initialState, { type: 'report_loaded', key: 'k', report: report() });
    s = reduce(s, { type: 'marker_opened', id: 'w1-f0' });
    s = reduce(s, { type: 'side_panel_picked', panel: 'ask' });
    expect(s.sidePanel).toBe('ask');
    expect(s.detailOpen).toBe(false);
  });

  it('opening a report starts on the findings list again', () => {
    let s = reduce(initialState, { type: 'side_panel_picked', panel: 'ask' });
    s = reduce(s, { type: 'report_loaded', key: 'k', report: report() });
    expect(s.sidePanel).toBe('findings');
  });
});

describe('asking, pending state', () => {
  it('remembers the question while it is being answered', () => {
    let s = reduce(initialState, { type: 'ask_submitted', jobId: 'j1', question: 'why?' });
    expect(s.ask.pendingQuestion).toBe('why?');
    s = reduce(s, { type: 'answer_received', answer: answerFixture() });
    expect(s.ask.pendingQuestion).toBeNull();
  });

  it('forgets the pending question when the ask fails', () => {
    let s = reduce(initialState, { type: 'ask_submitted', jobId: 'j1', question: 'why?' });
    s = reduce(s, { type: 'ask_failed' });
    expect(s.ask.pendingQuestion).toBeNull();
  });

  it('opening the ask panel sets the range around where the video is', () => {
    const s = reduce(
      { ...initialState, ask: { ...initialState.ask, start_s: 0, end_s: 0 } },
      { type: 'side_panel_picked', panel: 'ask', timestamp_s: 100 },
    );
    expect(s.ask.start_s).toBe(94);
    expect(s.ask.end_s).toBe(106);
  });

  it('switching to another panel leaves the range alone', () => {
    const s = reduce(initialState, { type: 'side_panel_picked', panel: 'coach', timestamp_s: 100 });
    expect(s.ask.start_s).toBe(0);
  });
});
