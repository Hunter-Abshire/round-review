import type { Clip, ReviewOptions, Settings } from '../shared/types';

/** m:ss, or h:mm:ss past an hour. Null durations read as a dash rather than "0:00". */
export const formatDuration = (seconds: number | null): string => {
  if (seconds === null || !Number.isFinite(seconds)) return '—';
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes);
  return `${hours > 0 ? `${hours}:` : ''}${mm}:${String(secs).padStart(2, '0')}`;
};

const UNITS: ReadonlyArray<[number, string]> = [
  [1024 ** 3, 'GB'],
  [1024 ** 2, 'MB'],
  [1024, 'KB'],
];

export const formatBytes = (bytes: number): string => {
  for (const [size, unit] of UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${Math.round(bytes)} B`;
};

export const formatRelativeTime = (epochSeconds: number, nowSeconds: number): string => {
  const delta = Math.max(0, nowSeconds - epochSeconds);
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
};

/** One line describing what pressing Analyze will actually do to this clip. */
export const reviewSummary = (clip: Clip, options: ReviewOptions, settings: Settings): string => {
  if (options.coverage === 'sampled') {
    return `${settings.windows_per_file} samples of ${settings.window_s}s spread across the clip`;
  }
  if (options.max_span_s) {
    const windows = Math.max(1, Math.floor(options.max_span_s / settings.window_s));
    return `first ${formatDuration(options.max_span_s)} of gameplay, about ${windows} windows`;
  }
  if (options.max_windows) {
    return `${options.max_windows} windows spread across the whole clip`;
  }
  const windows = clip.estimated_windows;
  return windows ? `whole clip, ${windows} windows of ${settings.window_s}s` : 'whole clip';
};
