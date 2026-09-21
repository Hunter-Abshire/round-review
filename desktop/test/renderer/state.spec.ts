import { initialState, reduce, type State } from '../../src/renderer/state';
import { clip, job, report } from './fixtures';
import { EMPTY_CONTEXT } from '../../src/shared/types';

const withClips = (): State =>
  reduce(initialState, {
    type: 'clips_loaded',
    clips: [clip(), clip({ name: 'b.mp4', key: 'ffffffffffffffff' })],
    warning: null,
  });

describe('reduce', () => {
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
      job: job({ status: 'running', windows_done: 1, windows_total: 3 }),
    });
    expect(s.clips[0]?.status).toBe('running');
    expect(s.jobs['job1']?.windows_done).toBe(1);
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

  it('opens a report and selects markers', () => {
    let s = reduce(withClips(), {
      type: 'report_loaded',
      key: '0123456789abcdef',
      report: report(),
    });
    expect(s.view).toBe('review');
    expect(s.reviewKey).toBe('0123456789abcdef');
    expect(s.markers).toHaveLength(3);
    expect(s.selectedMarkerId).toBeNull();
    s = reduce(s, { type: 'marker_selected', id: 'w1-f0' });
    expect(s.selectedMarkerId).toBe('w1-f0');
    s = reduce(s, { type: 'marker_selected', id: 'nope' });
    expect(s.selectedMarkerId).toBeNull();
    s = reduce(s, { type: 'back_to_list' });
    expect(s.view).toBe('list');
    expect(s.report).toBeNull();
    expect(s.markers).toEqual([]);
  });

  it('records errors without losing clips', () => {
    const s = reduce(withClips(), { type: 'error', message: 'boom' });
    expect(s.error).toBe('boom');
    expect(s.clips).toHaveLength(2);
  });
});

describe('context and knowledge', () => {
  it('starts empty and updates one field at a time, blank meaning null', () => {
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
    const s = reduce(initialState, { type: 'knowledge_loaded', knowledge });
    expect(s.knowledge).toBe(knowledge);
  });
});
