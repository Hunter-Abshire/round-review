import {
  collectMarkers,
  formatClock,
  markerPercent,
  nearestMarker,
} from '../../src/renderer/timeline';
import { finding, report } from './fixtures';
import { EMPTY_CONTEXT } from '../../src/shared/types';

describe('collectMarkers', () => {
  it('flattens windows into markers ordered by timestamp with stable ids', () => {
    const markers = collectMarkers(report());
    expect(markers.map(m => m.timestamp_s)).toEqual([64.4, 305, 310]);
    expect(markers.map(m => m.id)).toEqual(['w0-f0', 'w1-f0', 'w1-f1']);
    expect(markers[1]?.finding.category).toBe('utility');
    expect(markers[0]?.windowIndex).toBe(0);
  });

  it('returns an empty list when there are no findings', () => {
    expect(collectMarkers(report({ windows: [] }))).toEqual([]);
  });
});

describe('markerPercent', () => {
  it('maps a timestamp to a percentage of the duration, clamped', () => {
    expect(markerPercent(30, 600)).toBeCloseTo(5);
    expect(markerPercent(-5, 600)).toBe(0);
    expect(markerPercent(900, 600)).toBe(100);
  });
  it('is 0 for a non-positive duration', () => {
    expect(markerPercent(10, 0)).toBe(0);
  });
});

describe('nearestMarker', () => {
  const markers = collectMarkers(report());
  it('returns the marker within tolerance closest to the current time', () => {
    expect(nearestMarker(markers, 306, 2)?.id).toBe('w1-f0');
  });
  it('returns null when nothing is within tolerance', () => {
    expect(nearestMarker(markers, 200, 2)).toBeNull();
    expect(nearestMarker([], 0, 2)).toBeNull();
  });
  it('prefers the closer marker when two are in tolerance', () => {
    const m = collectMarkers(
      report({
        windows: [
          {
            index: 0,
            start_s: 0,
            end_s: 12,
            model_calls: 1,
            context: EMPTY_CONTEXT,
            situation: null,
            warnings: [],
            findings: [finding({ timestamp_s: 5 }), finding({ timestamp_s: 6 })],
          },
        ],
      }),
    );
    expect(nearestMarker(m, 5.8, 2)?.id).toBe('w0-f1');
  });
});

describe('formatClock', () => {
  it('renders m:ss', () => {
    expect(formatClock(64.4)).toBe('1:04');
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(3599)).toBe('59:59');
  });
});
