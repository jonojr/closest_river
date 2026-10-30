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

// A flowing line as one copy of `layer` per dash step, for animateFlow. The
// copies have ids `${layer.id}-0` and up, and only the first is visible.
//
// MapLibre 5.8 and later rebuild every tile of a source whenever a layer's
// line-dasharray changes, even to another constant, so stepping one layer's
// dashes reloaded the river tiles about 100 times a second, which slow phones
// can't keep up with. Each copy keeps its dashes, and animateFlow switches
// between them with line-opacity, which is only a repaint; a copy at opacity
// 0 isn't drawn.
//
// scale stretches the dash pattern (dash units are multiples of the line
// width, so thin lines need a larger scale to get readable dashes).
export function flowLayers(layer, { scale = 1 } = {}) {
  const opacity = layer.paint?.['line-opacity'] ?? 1;
  return FLOW_DASH_STEPS.map((dashes, step) => ({
    ...layer,
    id: flowLayerId(layer.id, step),
    paint: {
      ...layer.paint,
      'line-dasharray': dashes.map((d) => d * scale),
      'line-opacity': step === 0 ? opacity : 0,
    },
  }));
}

function flowLayerId(id, step) {
  return `${id}-${step}`;
}

// Every copy's id, for anything that shows or hides the whole flow.
export function flowLayerIds(id) {
  return FLOW_DASH_STEPS.map((_, step) => flowLayerId(id, step));
}

// Animate a flow added with flowLayers so the dashes drift downstream. Falls
// back to static dashes for people who have asked for reduced motion.
//
// Options: stepMs sets the speed.
//
// Returns { start, stop }: stop it while nothing is selected so the map isn't
// repainting continuously while it sits idle. Safe to leave running across a
// style change, and costs nothing while the layers are missing, hidden or
// zoomed out past their minzoom: steps are skipped, so the map isn't
// repainted.
export function animateFlow(map, layerId, { stepMs = 60 } = {}) {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let step = 0;
  let last = 0;
  let frameId = null;

  // A style change rebuilds the copies with the first one showing.
  map.on('style.load', () => {
    step = 0;
  });

  function isShowing() {
    const layer = map.getLayer(flowLayerId(layerId, 0));
    return (
      layer !== undefined &&
      map.getLayoutProperty(layer.id, 'visibility') !== 'none' &&
      map.getZoom() >= (layer.minzoom ?? 0)
    );
  }

  function frame(timestamp) {
    if (!reducedMotion.matches && timestamp - last >= stepMs && isShowing()) {
      last = timestamp;
      const showing = flowLayerId(layerId, step);
      const opacity = map.getPaintProperty(showing, 'line-opacity');
      step = (step + 1) % FLOW_DASH_STEPS.length;
      map.setPaintProperty(showing, 'line-opacity', 0);
      map.setPaintProperty(flowLayerId(layerId, step), 'line-opacity', opacity);
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
