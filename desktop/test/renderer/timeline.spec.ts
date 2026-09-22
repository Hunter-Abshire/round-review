import { pairwise } from './helpers';
import {
  collectMarkers,
  coverageBands,
  formatClock,
  markerPercent,
  nearestMarker,
  nextMarker,
  rulerTicks,
} from '../../src/renderer/timeline';
import { EMPTY_CONTEXT } from '../../src/shared/types';
import { finding, report } from './fixtures';

describe('collectMarkers', () => {
  it('flattens windows into markers ordered by timestamp with stable ids', () => {
    const markers = collectMarkers(report());
    expect(markers.map(m => m.timestamp_s)).toEqual([64.4, 305, 310]);
    expect(markers.map(m => m.id)).toEqual(['w0-f0', 'w1-f0', 'w1-f1']);
    expect(markers[1]?.finding.category).toBe('utility');
    expect(markers[0]?.windowIndex).toBe(0);
  });

  it('numbers markers for display in playback order', () => {
    expect(collectMarkers(report()).map(m => m.ordinal)).toEqual([1, 2, 3]);
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

describe('coverageBands', () => {
  it('turns reviewed windows into percentage bands over the whole clip', () => {
    const bands = coverageBands(report());
    expect(bands).toHaveLength(2);
    expect(bands[0]).toEqual({ leftPercent: 10, widthPercent: 2, index: 0, findings: 1 });
    expect(bands[1]?.leftPercent).toBeCloseTo(50);
    expect(bands[1]?.findings).toBe(2);
  });

  it('merges windows that tile contiguously so full coverage is one band', () => {
    const tiled = report({
      windows: [0, 12, 24].map(start => ({
        index: start / 12,
        start_s: start,
        end_s: start + 12,
        model_calls: 1,
        context: EMPTY_CONTEXT,
        situation: null,
        abstained_reason: null,
        strengths: [],
        findings: [],
        warnings: [],
      })),
      recording: { ...report().recording, duration_s: 36 },
    });
    const bands = coverageBands(tiled);
    expect(bands).toHaveLength(1);
    expect(bands[0]?.widthPercent).toBeCloseTo(100);
  });

  it('is empty when nothing was reviewed', () => {
    expect(coverageBands(report({ windows: [] }))).toEqual([]);
  });
});

describe('rulerTicks', () => {
  it('spaces ticks at a round interval with clock labels', () => {
    const ticks = rulerTicks(600);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks.length).toBeLessThanOrEqual(12);
    expect(ticks[0]).toEqual({ timestamp_s: 0, leftPercent: 0, label: '0:00' });
    for (const [a, b] of pairwise(ticks)) {
      expect(b.timestamp_s - a.timestamp_s).toBe(ticks[1]!.timestamp_s - ticks[0]!.timestamp_s);
    }
    expect(ticks[ticks.length - 1]!.timestamp_s).toBeLessThanOrEqual(600);
  });

  it('handles a short clip', () => {
    expect(rulerTicks(20).length).toBeGreaterThan(1);
  });

  it('is empty for an unknown duration', () => {
    expect(rulerTicks(0)).toEqual([]);
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
  it('keeps the current selection when two are equally close', () => {
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
            abstained_reason: null,
            strengths: [],
            warnings: [],
            findings: [finding({ timestamp_s: 5 }), finding({ timestamp_s: 7 })],
          },
        ],
      }),
    );
    expect(nearestMarker(m, 6, 2, 'w0-f1')?.id).toBe('w0-f1');
  });
});

describe('nextMarker', () => {
  const markers = collectMarkers(report());
  it('steps forward and backward through findings', () => {
    expect(nextMarker(markers, 'w0-f0', 1)?.id).toBe('w1-f0');
    expect(nextMarker(markers, 'w1-f0', -1)?.id).toBe('w0-f0');
  });
  it('stops at the ends', () => {
    expect(nextMarker(markers, 'w1-f1', 1)?.id).toBe('w1-f1');
    expect(nextMarker(markers, 'w0-f0', -1)?.id).toBe('w0-f0');
  });
  it('starts from the first finding when nothing is selected', () => {
    expect(nextMarker(markers, null, 1)?.id).toBe('w0-f0');
    expect(nextMarker([], null, 1)).toBeNull();
  });
});

describe('formatClock', () => {
  it('renders m:ss', () => {
    expect(formatClock(64.4)).toBe('1:04');
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(3599)).toBe('59:59');
  });
});
