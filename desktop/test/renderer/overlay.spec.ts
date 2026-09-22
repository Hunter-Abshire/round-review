/**
 * @jest-environment jsdom
 */
import { renderOverlay, shapeToPixels, videoContentRect } from '../../src/renderer/overlay';
import type { Shape } from '../../src/shared/types';

const box = (overrides: Partial<Shape> = {}): Shape => ({
  kind: 'box',
  label: 'the exposed angle',
  x: 0.25,
  y: 0.5,
  w: 0.25,
  h: 0.25,
  x2: 0,
  y2: 0,
  ...overrides,
});

describe('videoContentRect', () => {
  it('fills the element when the aspect ratios match', () => {
    expect(videoContentRect(1600, 900, 1920, 1080)).toEqual({
      x: 0,
      y: 0,
      width: 1600,
      height: 900,
    });
  });

  it('letterboxes a wide video in a tall element', () => {
    // 16:9 video inside a 16:12 box: bars top and bottom
    const rect = videoContentRect(1600, 1200, 1920, 1080);
    expect(rect.width).toBe(1600);
    expect(rect.height).toBe(900);
    expect(rect.y).toBe(150);
    expect(rect.x).toBe(0);
  });

  it('pillarboxes a tall video in a wide element', () => {
    const rect = videoContentRect(1600, 900, 1080, 1920);
    expect(rect.height).toBe(900);
    expect(rect.width).toBeCloseTo(506.25);
    expect(rect.x).toBeCloseTo(546.875);
  });

  it('falls back to the whole element before the video has loaded', () => {
    expect(videoContentRect(800, 450, 0, 0)).toEqual({ x: 0, y: 0, width: 800, height: 450 });
  });
});

describe('shapeToPixels', () => {
  const rect = { x: 100, y: 50, width: 800, height: 450 };

  it('maps a box into the drawn video area, not the element', () => {
    expect(shapeToPixels(box(), rect)).toEqual({
      kind: 'box',
      label: 'the exposed angle',
      x: 300,
      y: 275,
      width: 200,
      height: 112.5,
    });
  });

  it('maps a point to a small marker', () => {
    const point = shapeToPixels(box({ kind: 'point', x: 0.5, y: 0.5 }), rect);
    expect(point.kind).toBe('point');
    expect(point.x).toBe(500);
    expect(point.y).toBe(275);
  });

  it('maps an arrow end to end', () => {
    const arrow = shapeToPixels(box({ kind: 'arrow', x: 0, y: 0, x2: 1, y2: 1 }), rect);
    expect(arrow).toMatchObject({ kind: 'arrow', x: 100, y: 50, x2: 900, y2: 500 });
  });
});

describe('renderOverlay', () => {
  const rect = { x: 0, y: 0, width: 800, height: 450 };

  it('draws one shape per focus entry with its label', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    renderOverlay(svg, [box(), box({ kind: 'point', label: 'stand here' })], rect);
    expect(svg.querySelectorAll('[data-shape]')).toHaveLength(2);
    expect(svg.textContent).toContain('the exposed angle');
    expect(svg.textContent).toContain('stand here');
  });

  it('sizes itself to the drawn video area', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    renderOverlay(svg, [box()], { x: 10, y: 20, width: 640, height: 360 });
    expect(svg.getAttribute('viewBox')).toBe('0 0 650 380'); // 10+640 by 20+360
  });

  it('clears when there is nothing to draw', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    renderOverlay(svg, [box()], rect);
    renderOverlay(svg, [], rect);
    expect(svg.childElementCount).toBe(0);
  });

  it('draws nothing when the video area has no size yet', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    renderOverlay(svg, [box()], { x: 0, y: 0, width: 0, height: 0 });
    expect(svg.childElementCount).toBe(0);
  });
});
