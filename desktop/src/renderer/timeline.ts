import type { Finding, Report } from '../shared/types';

export interface Marker {
  id: string;
  ordinal: number;
  windowIndex: number;
  timestamp_s: number;
  finding: Finding;
}

/** A reviewed stretch of the recording, as percentages of the whole clip. */
export interface CoverageBand {
  leftPercent: number;
  widthPercent: number;
  index: number;
  findings: number;
}

export interface RulerTick {
  timestamp_s: number;
  leftPercent: number;
  label: string;
}

/** Flatten a report's windows into markers sorted by timestamp. Ids are stable per report. */
export const collectMarkers = (report: Report): Marker[] =>
  report.windows
    .flatMap(w =>
      w.findings.map((finding, i) => ({
        id: `w${w.index}-f${i}`,
        ordinal: 0,
        windowIndex: w.index,
        timestamp_s: finding.timestamp_s,
        finding,
      })),
    )
    .sort((a, b) => a.timestamp_s - b.timestamp_s)
    .map((marker, i) => ({ ...marker, ordinal: i + 1 }));

/** Horizontal position of a marker on the track, 0..100. */
export const markerPercent = (timestampS: number, durationS: number): number => {
  if (durationS <= 0) return 0;
  return Math.min(100, Math.max(0, (timestampS / durationS) * 100));
};

/**
 * Reviewed spans as bands over the whole clip, so it is obvious at a glance how much of the
 * recording was looked at. Windows that tile contiguously merge into one band, which is what
 * a full review looks like.
 */
export const coverageBands = (report: Report): CoverageBand[] => {
  const duration = report.recording.duration_s;
  if (duration <= 0 || report.windows.length === 0) return [];
  const ordered = [...report.windows].sort((a, b) => a.start_s - b.start_s);
  const bands: CoverageBand[] = [];
  let start = ordered[0]!.start_s;
  let end = ordered[0]!.end_s;
  let index = ordered[0]!.index;
  let findings = 0;

  const push = (): void => {
    bands.push({
      leftPercent: markerPercent(start, duration),
      widthPercent: markerPercent(end, duration) - markerPercent(start, duration),
      index,
      findings,
    });
  };

  for (const window of ordered) {
    if (window.start_s > end + 0.01) {
      push();
      start = window.start_s;
      index = window.index;
      findings = 0;
    }
    end = Math.max(end, window.end_s);
    findings += window.findings.length;
  }
  push();
  return bands;
};

const TICK_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];
const TARGET_TICKS = 8;

/** Evenly spaced time labels under the video, at a round interval. */
export const rulerTicks = (durationS: number): RulerTick[] => {
  if (durationS <= 0) return [];
  const ideal = durationS / TARGET_TICKS;
  const step =
    TICK_STEPS.find(candidate => candidate >= ideal) ?? TICK_STEPS[TICK_STEPS.length - 1]!;
  const ticks: RulerTick[] = [];
  for (let t = 0; t <= durationS; t += step) {
    ticks.push({ timestamp_s: t, leftPercent: markerPercent(t, durationS), label: formatClock(t) });
  }
  return ticks;
};

/** The marker closest to `currentTime` within `toleranceS`, preferring the current selection. */
export const nearestMarker = (
  markers: Marker[],
  currentTime: number,
  toleranceS: number,
  selectedId: string | null = null,
): Marker | null => {
  let best: Marker | null = null;
  let bestDistance = Infinity;
  for (const marker of markers) {
    const distance = Math.abs(marker.timestamp_s - currentTime);
    if (
      distance <= toleranceS &&
      (distance < bestDistance || (distance === bestDistance && marker.id === selectedId))
    ) {
      best = marker;
      bestDistance = distance;
    }
  }
  return best;
};

/** Step through findings in playback order; stops at the ends rather than wrapping. */
export const nextMarker = (
  markers: Marker[],
  selectedId: string | null,
  direction: 1 | -1,
): Marker | null => {
  if (markers.length === 0) return null;
  const current = markers.findIndex(m => m.id === selectedId);
  if (current === -1) return markers[direction === 1 ? 0 : markers.length - 1] ?? null;
  const next = Math.min(markers.length - 1, Math.max(0, current + direction));
  return markers[next] ?? null;
};

export const formatClock = (seconds: number): string => {
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
};
