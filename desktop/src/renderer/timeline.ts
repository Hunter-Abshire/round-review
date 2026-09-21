import type { Finding, Report } from '../shared/types';

export interface Marker {
  id: string;
  windowIndex: number;
  timestamp_s: number;
  finding: Finding;
}

/** Flatten a report's windows into markers sorted by timestamp. Ids are stable per report. */
export const collectMarkers = (report: Report): Marker[] =>
  report.windows
    .flatMap(w =>
      w.findings.map((finding, i) => ({
        id: `w${w.index}-f${i}`,
        windowIndex: w.index,
        timestamp_s: finding.timestamp_s,
        finding,
      })),
    )
    .sort((a, b) => a.timestamp_s - b.timestamp_s);

/** Horizontal position of a marker on the track, 0..100. */
export const markerPercent = (timestampS: number, durationS: number): number => {
  if (durationS <= 0) return 0;
  return Math.min(100, Math.max(0, (timestampS / durationS) * 100));
};

/** The marker closest to `currentTime` within `toleranceS`, or null. */
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

export const formatClock = (seconds: number): string => {
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
};
