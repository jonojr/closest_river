// Helpers for showing which way a river flows. OSM waterways are drawn
// downstream, so "forwards along the line" is the direction of flow.

// A right-pointing chevron, drawn as a canvas so it stays crisp on high-DPI
// screens and can carry an outline that reads against both the river line and
// the basemap. Symbol layers with 'symbol-placement': 'line' rotate it to
// follow the line.
export function chevronImage({ size, fill, outline, pixelRatio = 2 }) {
  const px = size * pixelRatio;
  const canvas = document.createElement('canvas');
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext('2d');

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(px * 0.35, px * 0.22);
  ctx.lineTo(px * 0.65, px * 0.5);
  ctx.lineTo(px * 0.35, px * 0.78);

  ctx.strokeStyle = outline;
  ctx.lineWidth = px * 0.3;
  ctx.stroke();
  ctx.strokeStyle = fill;
  ctx.lineWidth = px * 0.14;
  ctx.stroke();

  return {
    image: ctx.getImageData(0, 0, px, px),
    options: { pixelRatio },
  };
}

// Dash patterns that, stepped through in order, slide the dashes forwards
// along the line. Each step moves the dash half a unit; the cycle is one full
// dash + gap (3 + 4 units).
const FLOW_DASH_STEPS = [
  [0, 4, 3],
  [0.5, 4, 2.5],
  [1, 4, 2],
  [1.5, 4, 1.5],
  [2, 4, 1],
  [2.5, 4, 0.5],
  [3, 4, 0],
  [0, 0.5, 3, 3.5],
  [0, 1, 3, 3],
  [0, 1.5, 3, 2.5],
  [0, 2, 3, 2],
  [0, 2.5, 3, 1.5],
  [0, 3, 3, 1],
  [0, 3.5, 3, 0.5],
];

// Animate a dashed line layer so the dashes drift downstream. Falls back to a
// static dash for people who have asked for reduced motion.
//
// Options: stepMs sets the speed; scale stretches the dash pattern (dash units
// are multiples of the line width, so thin lines need a larger scale to get
// readable dashes). The layer's initial line-dasharray should be
// [0, 4 * scale, 3 * scale].
//
// Returns { start, stop }: stop it while nothing is selected so the map isn't
// repainting continuously while it sits idle. Safe to leave running across a
// style change, and costs nothing while the layer is missing, hidden or zoomed
// out past its minzoom: steps are skipped, so the map isn't repainted.
export function animateFlow(map, layerId, { stepMs = 60, scale = 1 } = {}) {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const steps = FLOW_DASH_STEPS.map((dashes) => dashes.map((d) => d * scale));
  let step = 0;
  let last = 0;
  let frameId = null;

  function isShowing() {
    const layer = map.getLayer(layerId);
    return (
      layer !== undefined &&
      map.getLayoutProperty(layerId, 'visibility') !== 'none' &&
      map.getZoom() >= (layer.minzoom ?? 0)
    );
  }

  function frame(timestamp) {
    if (!reducedMotion.matches && timestamp - last >= stepMs && isShowing()) {
      last = timestamp;
      step = (step + 1) % steps.length;
      map.setPaintProperty(layerId, 'line-dasharray', steps[step]);
    }
    frameId = requestAnimationFrame(frame);
  }

  return {
    start() {
      if (frameId === null) {
        frameId = requestAnimationFrame(frame);
      }
    },
    stop() {
      if (frameId !== null) {
        cancelAnimationFrame(frameId);
        frameId = null;
      }
    },
  };
}
