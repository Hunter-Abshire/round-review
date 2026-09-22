import type { Shape } from '../shared/types';

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PixelShape {
  kind: Shape['kind'];
  label: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
  x2?: number;
  y2?: number;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const POINT_RADIUS = 10;

/**
 * Where the video actually is inside its element.
 *
 * A video element letterboxes to preserve aspect, so the picture is usually smaller than the
 * box around it. Shapes are in fractions of the picture, so drawing them against the element
 * would put them in the wrong place on any clip whose shape differs from the window.
 */
export const videoContentRect = (
  elementWidth: number,
  elementHeight: number,
  videoWidth: number,
  videoHeight: number,
): Rect => {
  if (videoWidth <= 0 || videoHeight <= 0) {
    return { x: 0, y: 0, width: elementWidth, height: elementHeight };
  }
  const scale = Math.min(elementWidth / videoWidth, elementHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return { x: (elementWidth - width) / 2, y: (elementHeight - height) / 2, width, height };
};

export const shapeToPixels = (shape: Shape, rect: Rect): PixelShape => {
  const px = (fraction: number): number => rect.x + fraction * rect.width;
  const py = (fraction: number): number => rect.y + fraction * rect.height;
  if (shape.kind === 'box') {
    return {
      kind: 'box',
      label: shape.label,
      x: px(shape.x),
      y: py(shape.y),
      width: shape.w * rect.width,
      height: shape.h * rect.height,
    };
  }
  if (shape.kind === 'arrow') {
    return {
      kind: 'arrow',
      label: shape.label,
      x: px(shape.x),
      y: py(shape.y),
      x2: px(shape.x2),
      y2: py(shape.y2),
    };
  }
  return { kind: 'point', label: shape.label, x: px(shape.x), y: py(shape.y) };
};

const svgEl = <K extends keyof SVGElementTagNameMap>(
  tag: K,
  attributes: Record<string, string | number>,
): SVGElementTagNameMap[K] => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  return node;
};

const label = (text: string, x: number, y: number): SVGGElement => {
  const group = svgEl('g', {});
  const caption = svgEl('text', { x: x + 6, y: Math.max(14, y - 6), class: 'overlay-label' });
  caption.textContent = text;
  group.append(caption);
  return group;
};

/** Draw the model's focus shapes over the video. Emphasis, not measurement. */
export const renderOverlay = (svg: SVGSVGElement, shapes: Shape[], rect: Rect): void => {
  svg.replaceChildren();
  if (rect.width <= 0 || rect.height <= 0 || shapes.length === 0) return;
  svg.setAttribute('viewBox', `0 0 ${rect.x + rect.width} ${rect.y + rect.height}`);

  for (const shape of shapes) {
    const drawn = shapeToPixels(shape, rect);
    const group = svgEl('g', { class: `overlay-shape overlay-${drawn.kind}` });
    group.dataset['shape'] = drawn.kind;

    if (drawn.kind === 'box') {
      group.append(
        svgEl('rect', {
          x: drawn.x,
          y: drawn.y,
          width: drawn.width ?? 0,
          height: drawn.height ?? 0,
          rx: 6,
          class: 'overlay-box',
        }),
      );
      group.append(label(drawn.label, drawn.x, drawn.y));
    } else if (drawn.kind === 'arrow') {
      group.append(
        svgEl('line', {
          x1: drawn.x,
          y1: drawn.y,
          x2: drawn.x2 ?? 0,
          y2: drawn.y2 ?? 0,
          class: 'overlay-arrow',
          'marker-end': 'url(#overlay-arrowhead)',
        }),
      );
      group.append(label(drawn.label, drawn.x2 ?? 0, drawn.y2 ?? 0));
    } else {
      group.append(
        svgEl('circle', { cx: drawn.x, cy: drawn.y, r: POINT_RADIUS, class: 'overlay-point' }),
      );
      group.append(label(drawn.label, drawn.x + POINT_RADIUS, drawn.y));
    }
    svg.append(group);
  }
};
