import {
  formatBytes,
  formatDuration,
  formatRelativeTime,
  reviewSummary,
} from '../../src/renderer/format';
import { clip, settings } from './fixtures';

describe('formatDuration', () => {
  it('renders m:ss under an hour and h:mm:ss above', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(64.4)).toBe('1:04');
    expect(formatDuration(749)).toBe('12:29');
    expect(formatDuration(3725)).toBe('1:02:05');
  });
  it('renders an unknown duration as a dash', () => {
    expect(formatDuration(null)).toBe('—');
  });
});

describe('formatBytes', () => {
  it('scales to the nearest sensible unit', () => {
    expect(formatBytes(900)).toBe('900 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatBytes(3.2 * 1024 * 1024 * 1024)).toBe('3.2 GB');
  });
});

describe('formatRelativeTime', () => {
  const now = Date.UTC(2026, 8, 21, 12, 0, 0) / 1000;
  it('describes recent recordings in human terms', () => {
    expect(formatRelativeTime(now - 30, now)).toBe('just now');
    expect(formatRelativeTime(now - 60 * 5, now)).toBe('5m ago');
    expect(formatRelativeTime(now - 3600 * 3, now)).toBe('3h ago');
    expect(formatRelativeTime(now - 86400 * 2, now)).toBe('2d ago');
  });
});

describe('reviewSummary', () => {
  it('describes a full review with its window count', () => {
    const text = reviewSummary(
      clip(),
      { coverage: 'full', max_span_s: null, max_windows: null },
      settings(),
    );
    expect(text).toContain('47 windows');
    expect(text).toContain('whole clip');
  });

  it('describes a first-minute review', () => {
    const text = reviewSummary(
      clip(),
      { coverage: 'full', max_span_s: 60, max_windows: null },
      settings(),
    );
    expect(text).toContain('first 1:00');
    expect(text).not.toContain('47 windows');
  });

  it('describes a sampled review', () => {
    const text = reviewSummary(
      clip(),
      { coverage: 'sampled', max_span_s: null, max_windows: null },
      settings(),
    );
    expect(text).toContain('3 samples');
  });

  it('copes with an unreadable clip', () => {
    const text = reviewSummary(
      clip({ duration_s: null, estimated_windows: null }),
      { coverage: 'full', max_span_s: null, max_windows: null },
      settings(),
    );
    expect(text).toContain('whole clip');
    expect(text).not.toContain('undefined');
  });
});
