// The river map shared by the main page and the river detail page: themed
// basemap, the waterway network with flow direction, a highlighted river,
// popups and controls. Each page adds its own layers and UI on top.

import 'maplibre-gl/dist/maplibre-gl.css';
import '@mapbox-controls/ruler/src/index.css';

import RulerControl from '@mapbox-controls/ruler';
import maplibregl from 'maplibre-gl';
import { animateFlow, chevronImage } from './flowDirection';
import { MapLayersControl } from './MapLayersControl';

// Background maps, as MapTiler style ids for each theme. tone says which
// river palette reads against the map; it follows the theme unless the map
// is the same either way.
const BASEMAPS = {
  minimal: { label: 'Minimal', light: 'dataviz', dark: 'dataviz-dark' },
  streets: { label: 'Streets', light: 'streets-v2', dark: 'streets-v2-dark' },
  terrain: { label: 'Terrain', light: 'outdoor-v2', dark: 'outdoor-v2-dark' },
  // Imagery is dark whichever theme is on, so draw rivers in the dark palette.
  satellite: {
    label: 'Satellite',
    light: 'hybrid',
    dark: 'hybrid',
    tone: 'dark',
  },
};
const DEFAULT_BASEMAP = 'minimal';
const BASEMAP_STORAGE_KEY = 'basemap';

function styleUrl(styleId) {
  return `https://api.maptiler.com/maps/${styleId}/style.json?key=${process.env.MAPTILER_API_KEY}`;
}

function storedBasemap() {
  try {
    const key = localStorage.getItem(BASEMAP_STORAGE_KEY);
    return key in BASEMAPS ? key : DEFAULT_BASEMAP;
  } catch {
    return DEFAULT_BASEMAP;
  }
}

// Keep waterway blues in step with $primary in custom_bootstrap_vars.scss.
const PALETTES = {
  light: {
    river: '#1d5fc4',
    stream: '#6fa6e4',
    intermittent: '#8aa2bf',
    selectedRiver: '#0a3a82',
    selectedRiverCasing: '#ffffff',
    riverFlow: '#cfe3fb',
    selectedRiverFlow: '#8ec5ff',
    selectedArrowFill: '#ffffff',
    lineToRiver: '#334155',
    arrowOutline: '#ffffff',
  },
  dark: {
    river: '#5aa9ff',
    stream: '#3b6aa3',
    intermittent: '#5b7290',
    selectedRiver: '#9fd2ff',
    selectedRiverCasing: '#0b1220',
    riverFlow: '#1b3d6b',
    selectedRiverFlow: '#1e5bb0',
    selectedArrowFill: '#0b1220',
    lineToRiver: '#cbd5e1',
    arrowOutline: '#0b1220',
  },
};

export const EMPTY = { type: 'FeatureCollection', features: [] };

// Waterways that are dry for part of the year, drawn paler, dashed and without
// animated flow. Most of inland Australia is like this, and drawing it as
// permanent water is misleading. The tiles carry a precomputed flag; the
// highlighted river's GeoJSON has the raw tags, so it is tested the same way
// rivers/models.py:is_intermittent() does.
const INTERMITTENT = ['to-boolean', ['get', 'intermittent']];
const PERENNIAL = ['!', INTERMITTENT];
const INTERMITTENT_TAGS = [
  'any',
  ['==', ['get', 'intermittent', ['get', 'tags']], 'yes'],
  [
    'in',
    ['get', 'seasonal', ['get', 'tags']],
    ['literal', ['yes', 'wet_season', 'wet']],
  ],
];

// Solid when zoomed out, where dashes on a hairline just look broken; dashed
// once lines are wide enough for the pattern to read.
const INTERMITTENT_DASH = [
  'step',
  ['zoom'],
  ['literal', [1, 0]],
  9,
  ['literal', [3, 2]],
];

// Gentler and slower than the highlighted river, with a stretched dash so it
// reads as a drift rather than a shimmer on the thinner lines.
const NETWORK_FLOW_SCALE = 2;

// Set on <html> by base.html and theme.js. Read directly rather than importing
// theme.js, which already runs once in the project bundle.
function currentTheme() {
  return document.documentElement.getAttribute('data-bs-theme') === 'dark'
    ? 'dark'
    : 'light';
}

// A zoom-interpolated value with every output scaled by factor. Expressions
// can't multiply a zoom interpolation, so the stops are scaled up front.
function zoomRamp(stops, factor = 1) {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    ...stops.flatMap(([zoom, value]) => [zoom, value * factor]),
  ];
}

////////////
// Popups //
////////////

// Only allow links to our own pages or to http(s) URLs.
function safeHref(link) {
  return /^(https?:\/\/|\/)/.test(link) ? link : null;
}

// Rows of { label, text, link? } as a key/value table. Built from DOM nodes,
// not an HTML string: the values are OSM tags and can contain anything.
function popupTable(rows) {
  const table = document.createElement('dl');
  table.className = 'popup-table';
  for (const { label, text, link } of rows) {
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    const href = link && safeHref(link);
    if (href) {
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.textContent = text;
      if (!href.startsWith('/')) {
        anchor.target = '_blank';
        anchor.rel = 'noopener';
      }
      detail.appendChild(anchor);
    } else {
      detail.textContent = text;
    }
    table.append(term, detail);
  }
  return table;
}

export function popupNote(text) {
  const note = document.createElement('p');
  note.className = 'popup-note';
  note.textContent = text;
  return note;
}

// Tags worth showing up front, in this order. Everything else (often bulk
// import bookkeeping like pfi/ufi/nfeat_id, which can run to dozens of rows)
// goes behind a "More OSM tags" expander.
const FEATURED_TAGS = [
  'Name',
  'destination',
  'Wikipedia',
  'Wikidata',
  'waterway',
  'intermittent',
  'seasonal',
  'tidal',
];

function waterwayPopupContent(data) {
  const rows = Object.entries(data).map(([label, value]) => ({
    label,
    ...value,
  }));
  const featured = FEATURED_TAGS.flatMap((label) =>
    rows.filter((row) => row.label === label),
  );
  const others = rows.filter((row) => !FEATURED_TAGS.includes(row.label));

  const content = document.createElement('div');
  content.className = 'popup-body';
  content.appendChild(popupTable(featured));
  if (others.length) {
    const details = document.createElement('details');
    details.className = 'popup-more';
    const summary = document.createElement('summary');
    summary.textContent = `More OSM tags (${others.length})`;
    details.append(summary, popupTable(others));
    content.appendChild(details);
  }
  return content;
}

// Section id from either source: the vector tiles carry osm_way_id; the
// highlighted river's GeoJSON (from Django's serializer) carries it as pk. At
// cached zooms the stream layer is merged into a single geometry to keep
// tiles small, so individual ways have no id and there is nothing to look up.
// Zooming in past the cache threshold (see CACHE_BELOW_ZOOM_LEVEL) un-merges
// them and makes them clickable again.
function sectionId(feature) {
  return feature.properties.osm_way_id ?? feature.properties.pk;
}

/**
 * Create the river map.
 *
 * options.dimNetwork: fade the surrounding rivers and streams so the
 *   highlighted river stands out (the river detail page).
 * options.addExtraLayers(map, colours, beforeId): add page-specific layers.
 *   Called on every style load, after the river layers.
 * options.extraPopups: { layerId: (lngLat) => Node } for page-specific
 *   layers. These take priority over the waterway popups.
 *
 * Returns { map, popup, setRiver(geojson), flow } where flow starts and
 * stops the highlighted river's animation.
 */
export function createRiverMap({
  container = 'map',
  dimNetwork = false,
  addExtraLayers = () => {},
  extraPopups = {},
} = {}) {
  let basemap = storedBasemap();
  const basemapStyle = () => styleUrl(BASEMAPS[basemap][currentTheme()]);
  const palette = () => PALETTES[BASEMAPS[basemap].tone ?? currentTheme()];

  const map = new maplibregl.Map({
    container,
    style: basemapStyle(),
    center: [135.0951931, -26.3911232],
    zoom: 3, // starting zoom
  });

  // The highlighted river, kept outside the map so it can be put back after a
  // basemap swap (setStyle throws away every source and layer).
  let river = EMPTY;
  const flow = animateFlow(map, 'river-flow');
  const networkFlow = animateFlow(map, 'river-section-flow', {
    stepMs: 90,
    scale: NETWORK_FLOW_SCALE,
  });
  networkFlow.start();

  const networkOpacity = dimNetwork ? 0.45 : 1;

  // Everything we draw on top of the basemap. Runs on every style load: the
  // first one, and again whenever the theme changes the basemap.
  function addRiverLayers() {
    const colours = palette();

    // Insert our layers below the labels so place names stay on top. That's
    // the run of symbol layers at the end of the style, not simply the first
    // symbol layer: Terrain (outdoor-v2) has contour labels early on, under
    // its water and land fills, which would bury the rivers.
    const layers = map.getStyle().layers;
    let labelsStart = layers.length;
    while (labelsStart > 0 && layers[labelsStart - 1].type === 'symbol') {
      labelsStart -= 1;
    }
    const firstSymbolId = layers[labelsStart]?.id;

    const arrows = {
      'network-arrow': chevronImage({
        size: 12,
        fill: colours.river,
        outline: colours.arrowOutline,
      }),
      'intermittent-arrow': chevronImage({
        size: 11,
        fill: colours.intermittent,
        outline: colours.arrowOutline,
      }),
      'stream-arrow': chevronImage({
        size: 10,
        fill: colours.stream,
        outline: colours.arrowOutline,
      }),
      'selected-arrow': chevronImage({
        size: 18,
        fill: colours.selectedArrowFill,
        outline: colours.selectedRiver,
      }),
    };
    for (const [id, arrow] of Object.entries(arrows)) {
      map.addImage(id, arrow.image, arrow.options);
    }

    map.addSource('river-sections-data', {
      type: 'vector',
      url: '/rivers/river-sections/tiles.json',
    });
    // Streams first, so rivers draw on top of the wash.
    //
    // A lighter tint and thinner than rivers, and faint when zoomed out: at
    // low zoom the stream network is dense enough that drawing it at full
    // weight turns into a solid block of colour. Kept subordinate it reads as
    // texture with the rivers standing out, but is still easy to follow once
    // zoomed in.
    const streamWidth = zoomRamp([
      [4, 0.6],
      [9, 1],
      [12, 1.5],
      [14, 2],
    ]);
    map.addLayer(
      {
        id: 'stream-sections-intermittent',
        type: 'line',
        source: 'river-sections-data',
        'source-layer': 'stream-sections',
        filter: INTERMITTENT,
        layout: {
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': colours.intermittent,
          'line-opacity': zoomRamp(
            [
              [4, 0.25],
              [8, 0.35],
              [11, 0.55],
              [14, 0.7],
            ],
            networkOpacity,
          ),
          'line-width': streamWidth,
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'stream-sections',
        type: 'line',
        source: 'river-sections-data',
        'source-layer': 'stream-sections',
        filter: PERENNIAL,
        layout: {
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': colours.stream,
          'line-opacity': zoomRamp(
            [
              [4, 0.35],
              [8, 0.5],
              [11, 0.8],
              [14, 0.95],
            ],
            networkOpacity,
          ),
          // Hairline when zoomed out, approaching the river weight as you zoom in.
          'line-width': streamWidth,
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'stream-section-arrows',
        type: 'symbol',
        source: 'river-sections-data',
        'source-layer': 'stream-sections',
        filter: PERENNIAL,
        minzoom: 13,
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 140,
          'icon-image': 'stream-arrow',
        },
        paint: {
          'icon-opacity': networkOpacity,
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'river-sections-intermittent',
        type: 'line',
        source: 'river-sections-data',
        'source-layer': 'river-sections',
        filter: INTERMITTENT,
        layout: {
          'line-join': 'round',
        },
        paint: {
          'line-color': colours.intermittent,
          'line-opacity': 0.9 * networkOpacity,
          'line-width': zoomRamp([
            [4, 0.8],
            [9, 1.6],
            [12, 2.2],
            [14, 2.6],
          ]),
          'line-dasharray': INTERMITTENT_DASH,
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'river-sections',
        type: 'line',
        source: 'river-sections-data',
        'source-layer': 'river-sections',
        filter: PERENNIAL,
        layout: {
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': colours.river,
          'line-opacity': networkOpacity,
          'line-width': zoomRamp([
            [4, 1],
            [9, 2.2],
            [12, 3],
            [14, 3.5],
          ]),
        },
      },
      firstSymbolId,
    );
    // Subtle downstream drift along every river (see animateFlow). Not below
    // zoom 9: there the network is too dense for movement to mean anything,
    // and it would keep the whole map repainting.
    map.addLayer(
      {
        id: 'river-section-flow',
        type: 'line',
        source: 'river-sections-data',
        'source-layer': 'river-sections',
        // Water moving along a channel that is usually dry would mislead.
        filter: PERENNIAL,
        minzoom: 9,
        layout: {
          'line-join': 'round',
        },
        paint: {
          'line-color': colours.riverFlow,
          'line-opacity': 0.7 * networkOpacity,
          'line-width': zoomRamp([
            [9, 0.8],
            [14, 1.4],
          ]),
          'line-dasharray': [0, 4 * NETWORK_FLOW_SCALE, 3 * NETWORK_FLOW_SCALE],
        },
      },
      firstSymbolId,
    );
    // Arrows, separately for each class so hiding intermittent waterways
    // hides their arrows too. Below zoom 10 the arrows are too small to read
    // and just thicken the network into noise.
    for (const [id, filter, image] of [
      ['river-section-arrows', PERENNIAL, 'network-arrow'],
      ['river-section-arrows-intermittent', INTERMITTENT, 'intermittent-arrow'],
    ]) {
      map.addLayer(
        {
          id,
          type: 'symbol',
          source: 'river-sections-data',
          'source-layer': 'river-sections',
          filter,
          minzoom: 10,
          layout: {
            'symbol-placement': 'line',
            'symbol-spacing': 120,
            'icon-image': image,
          },
          paint: {
            'icon-opacity': networkOpacity,
          },
        },
        firstSymbolId,
      );
    }

    map.addSource('river', { type: 'geojson', data: river });
    map.addLayer(
      {
        id: 'river-casing',
        type: 'line',
        source: 'river',
        layout: {
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': colours.selectedRiverCasing,
          'line-width': 9,
          'line-opacity': 0.9,
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'river',
        type: 'line',
        source: 'river',
        filter: ['!', INTERMITTENT_TAGS],
        layout: {
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': colours.selectedRiver,
          'line-width': 5,
        },
      },
      firstSymbolId,
    );
    // Stretches that are often dry: same weight, dashed over the casing.
    map.addLayer(
      {
        id: 'river-intermittent',
        type: 'line',
        source: 'river',
        filter: INTERMITTENT_TAGS,
        layout: {
          'line-join': 'round',
        },
        paint: {
          'line-color': colours.selectedRiver,
          'line-width': 5,
          'line-dasharray': [1.6, 1],
        },
      },
      firstSymbolId,
    );
    // Dashes that drift downstream along the highlighted river (see
    // animateFlow).
    map.addLayer(
      {
        id: 'river-flow',
        type: 'line',
        source: 'river',
        filter: ['!', INTERMITTENT_TAGS],
        layout: {
          'line-join': 'round',
        },
        paint: {
          'line-color': colours.selectedRiverFlow,
          'line-width': 3,
          'line-dasharray': [0, 4, 3],
        },
      },
      firstSymbolId,
    );
    map.addLayer(
      {
        id: 'arrows',
        type: 'symbol',
        source: 'river',
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 220,
          'icon-image': 'selected-arrow',
        },
      },
      firstSymbolId,
    );

    addExtraLayers(map, colours, firstSymbolId);
  }

  map.on('style.load', addRiverLayers);

  // diff: false forces a clean reload so 'style.load' fires and our layers
  // are rebuilt in the palette for the new map.
  function reloadBasemap() {
    map.setStyle(basemapStyle(), { diff: false });
  }

  document.addEventListener('themechange', reloadBasemap);

  function selectBasemap(key) {
    basemap = key;
    try {
      localStorage.setItem(BASEMAP_STORAGE_KEY, key);
    } catch {
      // Storage blocked: the choice just won't persist.
    }
    reloadBasemap();
  }

  // One popup at a time: clicking elsewhere moves it rather than stacking a
  // new one on top.
  const popup = new maplibregl.Popup({ maxWidth: '320px' });

  // Click targets, most specific first. Within a click, the topmost rendered
  // feature wins, so overlapping layers only ever produce one popup.
  const clickableLayers = [
    ...Object.keys(extraPopups),
    'arrows',
    'river',
    'river-intermittent',
    'river-sections',
    'river-sections-intermittent',
    'stream-sections',
    'stream-sections-intermittent',
  ];

  let popupRequest = 0;

  function showWaterwayPopup(lngLat, osmWayId) {
    const request = ++popupRequest;
    popup.setLngLat(lngLat).setDOMContent(popupNote('Loading…')).addTo(map);

    fetch(`/rivers/river-sections/${osmWayId}/popup_data/`)
      .then((response) => response.json())
      .then((data) => {
        // A later click has already replaced this popup.
        if (request !== popupRequest) {
          return;
        }
        popup.setDOMContent(waterwayPopupContent(data));
      })
      .catch(() => {
        if (request === popupRequest) {
          popup.setDOMContent(popupNote("Couldn't load details."));
        }
      });
  }

  map.on('click', (e) => {
    // A few pixels of slop: the stream lines are only a pixel or two wide.
    const slop = 4;
    const features = map.queryRenderedFeatures(
      [
        [e.point.x - slop, e.point.y - slop],
        [e.point.x + slop, e.point.y + slop],
      ],
      { layers: clickableLayers.filter((id) => map.getLayer(id)) },
    );

    const feature = features.find(
      (f) => f.layer.id in extraPopups || sectionId(f) !== undefined,
    );
    if (!feature) {
      return;
    }

    if (feature.layer.id in extraPopups) {
      ++popupRequest;
      popup
        .setLngLat(e.lngLat)
        .setDOMContent(extraPopups[feature.layer.id](e.lngLat))
        .addTo(map);
      return;
    }

    showWaterwayPopup(e.lngLat, sectionId(feature));
  });

  for (const layer of clickableLayers) {
    map.on('mouseenter', layer, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', layer, () => {
      map.getCanvas().style.cursor = '';
    });
  }

  const nav = new maplibregl.NavigationControl({ visualizePitch: true });
  map.addControl(nav, 'top-right');

  map.addControl(new RulerControl(), 'top-right');

  // Rivers always stay on; the stream network can be hidden to declutter.
  // Added after the 'style.load' handler above so it finds the layers built.
  map.addControl(
    new MapLayersControl({
      basemaps: {
        options: Object.entries(BASEMAPS).map(([key, { label }]) => ({
          key,
          label,
        })),
        selected: basemap,
        onSelect: selectBasemap,
      },
      groups: [
        {
          key: 'streams',
          label: 'Creeks & streams',
          layers: [
            'stream-sections',
            'stream-section-arrows',
            'stream-sections-intermittent',
          ],
        },
        {
          key: 'intermittent',
          label: 'Intermittent waterways',
          layers: [
            'river-sections-intermittent',
            'river-section-arrows-intermittent',
            'stream-sections-intermittent',
          ],
        },
        {
          key: 'flow',
          label: 'Animate river flow',
          layers: ['river-section-flow'],
        },
      ],
    }),
    'top-right',
  );

  const scale = new maplibregl.ScaleControl({
    maxWidth: 80,
    unit: 'metric',
  });
  map.addControl(scale, 'bottom-right');

  return {
    map,
    popup,
    flow,
    setRiver(geojson) {
      river = geojson;
      map.getSource('river')?.setData(geojson);
    },
  };
}
