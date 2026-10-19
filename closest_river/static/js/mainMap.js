import 'maplibre-gl/dist/maplibre-gl.css';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';
import '@mapbox-controls/ruler/src/index.css';

import RulerControl from '@mapbox-controls/ruler';
import maplibregl from 'maplibre-gl';
import MaplibreGeocoder from '@maplibre/maplibre-gl-geocoder';
import { animateFlow, chevronImage } from './flowDirection';
import { LayerToggleControl } from './LayerToggleControl';

const BASEMAPS = {
  light: `https://api.maptiler.com/maps/dataviz/style.json?key=${process.env.MAPTILER_API_KEY}`,
  dark: `https://api.maptiler.com/maps/dataviz-dark/style.json?key=${process.env.MAPTILER_API_KEY}`,
};

// Keep waterway blues in step with $primary in custom_bootstrap_vars.scss.
const PALETTES = {
  light: {
    river: '#1d5fc4',
    stream: '#6fa6e4',
    selectedRiver: '#0a3a82',
    selectedRiverCasing: '#ffffff',
    selectedRiverFlow: '#8ec5ff',
    selectedArrowFill: '#ffffff',
    lineToRiver: '#334155',
    arrowOutline: '#ffffff',
  },
  dark: {
    river: '#5aa9ff',
    stream: '#3b6aa3',
    selectedRiver: '#9fd2ff',
    selectedRiverCasing: '#0b1220',
    selectedRiverFlow: '#1e5bb0',
    selectedArrowFill: '#0b1220',
    lineToRiver: '#cbd5e1',
    arrowOutline: '#0b1220',
  },
};

const EMPTY = { type: 'FeatureCollection', features: [] };

// Set on <html> by base.html and theme.js. Read directly rather than importing
// theme.js, which already runs once in the project bundle.
function currentTheme() {
  'use strict';
  return document.documentElement.getAttribute('data-bs-theme') === 'dark'
    ? 'dark'
    : 'light';
}

const map = new maplibregl.Map({
  container: 'map', // container id
  style: BASEMAPS[currentTheme()],
  center: [135.0951931, -26.3911232],
  zoom: 3, // starting zoom
});

// The finder and result panels float over the map: down the left on wider
// screens, top and bottom on phones. Pad the camera so results land in the
// part of the map that is still visible.
const wideScreen = window.matchMedia('(min-width: 768px)');
function cameraPadding() {
  'use strict';
  return wideScreen.matches
    ? { top: 40, right: 70, bottom: 40, left: 380 }
    : { top: 260, right: 60, bottom: 290, left: 30 };
}

// What is currently selected, kept outside the map so it can be put back
// after a basemap swap (setStyle throws away every source and layer).
const selection = { river: EMPTY, lineToRiver: EMPTY };
const flow = animateFlow(map, 'river-flow');

// Everything we draw on top of the basemap. Runs on every style load: the
// first one, and again whenever the theme changes the basemap.
function addRiverLayers() {
  'use strict';
  const colours = PALETTES[currentTheme()];

  // Insert our layers below the first label layer so place names stay on top.
  const firstSymbolId = map
    .getStyle()
    .layers.find((layer) => layer.type === 'symbol')?.id;

  const arrows = {
    'network-arrow': chevronImage({
      size: 12,
      fill: colours.river,
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
  // A lighter tint and thinner than rivers, and faint when zoomed out: at low
  // zoom the stream network is dense enough that drawing it at full weight
  // turns into a solid block of colour. Kept subordinate it reads as texture
  // with the rivers standing out, but is still easy to follow once zoomed in.
  map.addLayer(
    {
      id: 'stream-sections',
      type: 'line',
      source: 'river-sections-data',
      'source-layer': 'stream-sections',
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': colours.stream,
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4,
          0.35,
          8,
          0.5,
          11,
          0.8,
          14,
          0.95,
        ],
        // Hairline when zoomed out, approaching the river weight as you zoom in.
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4,
          0.6,
          9,
          1,
          12,
          1.5,
          14,
          2,
        ],
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
      minzoom: 13,
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': 140,
        'icon-image': 'stream-arrow',
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
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': colours.river,
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4,
          1,
          9,
          2.2,
          12,
          3,
          14,
          3.5,
        ],
      },
    },
    firstSymbolId,
  );
  map.addLayer(
    {
      id: 'river-section-arrows',
      type: 'symbol',
      source: 'river-sections-data',
      'source-layer': 'river-sections',
      // Below this the arrows are too small to read and just thicken the
      // network into noise.
      minzoom: 10,
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': 120,
        'icon-image': 'network-arrow',
      },
    },
    firstSymbolId,
  );

  map.addSource('river', { type: 'geojson', data: selection.river });
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
  // Dashes that drift downstream along the selected river (see animateFlow).
  map.addLayer(
    {
      id: 'river-flow',
      type: 'line',
      source: 'river',
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

  map.addSource('line_to_river', {
    type: 'geojson',
    data: selection.lineToRiver,
  });
  map.addLayer(
    {
      id: 'line_to_river',
      type: 'line',
      source: 'line_to_river',
      layout: {
        'line-cap': 'round',
      },
      paint: {
        'line-color': colours.lineToRiver,
        'line-width': 3,
        'line-dasharray': [0.5, 2],
      },
    },
    firstSymbolId,
  );
}

map.on('style.load', addRiverLayers);

document.addEventListener('themechange', (event) => {
  'use strict';
  // diff: false forces a clean reload so 'style.load' fires and our layers
  // are rebuilt in the new palette.
  map.setStyle(BASEMAPS[event.detail.theme], { diff: false });
});

////////////
// Popups //
////////////

// One popup at a time: clicking elsewhere moves it rather than stacking a
// new one on top.
const popup = new maplibregl.Popup({ maxWidth: '320px' });

// Click targets, most specific first. Within a click, the topmost rendered
// feature wins, so overlapping layers only ever produce one popup.
const CLICKABLE_LAYERS = [
  'line_to_river',
  'arrows',
  'river',
  'river-sections',
  'stream-sections',
];

// Only allow links to our own pages or to http(s) URLs.
function safeHref(link) {
  'use strict';
  return /^(https?:\/\/|\/)/.test(link) ? link : null;
}

// Rows of { label, text, link? } as a key/value table. Built from DOM nodes,
// not an HTML string: the values are OSM tags and can contain anything.
function popupTable(rows) {
  'use strict';
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

function popupNote(text) {
  'use strict';
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
  'use strict';
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

let popupRequest = 0;

function showWaterwayPopup(lngLat, osmWayId) {
  'use strict';
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

// Section id from either source: the vector tiles carry osm_way_id; the
// selected river's GeoJSON (from Django's serializer) carries it as pk. At
// cached zooms the stream layer is merged into a single geometry to keep
// tiles small, so individual ways have no id and there is nothing to look up.
// Zooming in past the cache threshold (see CACHE_BELOW_ZOOM_LEVEL) un-merges
// them and makes them clickable again.
function sectionId(feature) {
  'use strict';
  return feature.properties.osm_way_id ?? feature.properties.pk;
}

let distanceToClosestRiver = 0;

map.on('click', (e) => {
  'use strict';
  // A few pixels of slop: the stream lines are only a pixel or two wide.
  const slop = 4;
  const features = map.queryRenderedFeatures(
    [
      [e.point.x - slop, e.point.y - slop],
      [e.point.x + slop, e.point.y + slop],
    ],
    { layers: CLICKABLE_LAYERS.filter((id) => map.getLayer(id)) },
  );

  const feature = features.find(
    (f) => f.layer.id === 'line_to_river' || sectionId(f) !== undefined,
  );
  if (!feature) {
    return;
  }

  if (feature.layer.id === 'line_to_river') {
    popup
      .setLngLat(e.lngLat)
      .setDOMContent(
        popupNote(
          `Distance to the closest river: ${distanceToClosestRiver} km`,
        ),
      )
      .addTo(map);
    return;
  }

  showWaterwayPopup(e.lngLat, sectionId(feature));
});

for (const layer of CLICKABLE_LAYERS) {
  map.on('mouseenter', layer, () => {
    map.getCanvas().style.cursor = 'pointer';
  });
  map.on('mouseleave', layer, () => {
    map.getCanvas().style.cursor = '';
  });
}

//////////////
// Controls //
//////////////

const nav = new maplibregl.NavigationControl({ visualizePitch: true });
map.addControl(nav, 'top-right');

map.addControl(new RulerControl(), 'top-right');

// Rivers always stay on; the stream network can be hidden to declutter.
map.addControl(
  new LayerToggleControl([
    {
      key: 'streams',
      label: 'Creeks & streams',
      layers: ['stream-sections', 'stream-section-arrows'],
    },
  ]),
  'top-right',
);

const scale = new maplibregl.ScaleControl({
  maxWidth: 80,
  unit: 'metric',
});
map.addControl(scale, 'bottom-right');

const marker = new maplibregl.Marker({ color: '#0b63c5' });

/////////////////
// Find nearest //
/////////////////

const riverCard = document.querySelector('#river-card');
const status = document.querySelector('#status');
const anyWaterway = document.querySelector('#any-waterway');

let lastPosition = null;

function setSelection({ river, lineToRiver }) {
  selection.river = river;
  selection.lineToRiver = lineToRiver;
  map.getSource('river')?.setData(river);
  map.getSource('line_to_river')?.setData(lineToRiver);
}

// Closing the result card un-selects everything it was describing.
function clearSelection() {
  'use strict';
  riverCard.hidden = true;
  setSelection({ river: EMPTY, lineToRiver: EMPTY });
  marker.remove();
  popup.remove();
  flow.stop();
  lastPosition = null;
}

document
  .querySelector('#river-card-close')
  .addEventListener('click', clearSelection);

// Fill a result field, hiding its row entirely when there is nothing to show
// rather than leaving an empty label.
function setField(element, text, href) {
  'use strict';
  const row = element.closest('dl > div');
  element.textContent = text || '';
  if (href !== undefined) {
    element.href = text ? href : '';
  }
  if (row) {
    row.hidden = !text;
  }
}

function mapPosition(latitude, longitude) {
  'use strict';
  const name = document.querySelector('#name');
  const eyebrow = document.querySelector('#river-card-eyebrow');
  const wikipedia = document.querySelector('#wikipedia');
  const destination = document.querySelector('#destination');
  const distance = document.querySelector('#distance');

  const includeStreams = anyWaterway.checked;
  const noun = includeStreams ? 'waterway' : 'river';

  lastPosition = [latitude, longitude];
  riverCard.hidden = true;
  status.textContent = `Finding the nearest ${noun}…`;

  marker.setLngLat([longitude, latitude]).addTo(map);

  map.flyTo({
    center: [longitude, latitude],
    zoom: 12,
    padding: cameraPadding(),
    essential: true, // this animation is considered essential with respect to prefers-reduced-motion
  });

  const query = includeStreams ? '?waterway=any' : '';
  fetch(`/rivers/closest_river/${latitude}/${longitude}/${query}`)
    .then((response) => response.json())
    .then((data) => {
      const feature = data.section.river ? data.river : data.section;

      eyebrow.textContent = `Your closest ${noun}`;
      name.textContent =
        feature?.name || `Unnamed ${data.section.waterway || 'waterway'}`;
      if (data.section.river) {
        name.href = `/rivers/${data.river.osm_id}/`;
      } else {
        name.removeAttribute('href');
      }
      setField(destination, feature?.destination);
      setField(
        wikipedia,
        feature?.wikipedia,
        `https://en.wikipedia.org/wiki/${feature?.wikipedia}`,
      );
      setField(distance, `${data.distance} km`);

      distanceToClosestRiver = data.distance;
      status.textContent = '';
      riverCard.hidden = false;

      setSelection({
        river: data.geometry ? JSON.parse(data.geometry) : EMPTY,
        lineToRiver: {
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'LineString',
            coordinates: [[longitude, latitude], data.closest_point_on_river],
          },
        },
      });
      flow.start();

      map.fitBounds(
        new maplibregl.LngLatBounds(
          [longitude, latitude],
          [longitude, latitude],
        ).extend(data.closest_point_on_river),
        { padding: cameraPadding(), maxZoom: 13, essential: true },
      );
    })
    .catch(() => {
      status.textContent = `Couldn't find a ${noun} near there. Try again?`;
    });
}

// Remember the switch between visits, and re-run the current search when it
// changes so the result always matches what the switch says.
try {
  anyWaterway.checked = localStorage.getItem('anyWaterway') === 'true';
} catch {
  // Storage blocked: default to rivers only.
}
anyWaterway.addEventListener('change', () => {
  'use strict';
  try {
    localStorage.setItem('anyWaterway', String(anyWaterway.checked));
  } catch {
    // Storage blocked: the choice just won't persist.
  }
  if (lastPosition) {
    mapPosition(...lastPosition);
  }
});

const geocoderApi = {
  forwardGeocode: async (config) => {
    'use strict';
    const features = [];
    try {
      const request = `https://nominatim.openstreetmap.org/search?q=${config.query}&format=geojson&polygon_geojson=1&addressdetails=1`;
      const response = await fetch(request);
      const geojson = await response.json();
      for (const feature of geojson.features) {
        const center = [
          feature.bbox[0] + (feature.bbox[2] - feature.bbox[0]) / 2,
          feature.bbox[1] + (feature.bbox[3] - feature.bbox[1]) / 2,
        ];
        const point = {
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: center,
          },
          place_name: feature.properties.display_name,
          properties: feature.properties,
          text: feature.properties.display_name,
          place_type: ['place'],
          center,
        };
        features.push(point);
      }
      const point = features[0].geometry.coordinates;
      mapPosition(point[1], point[0]);
    } catch (e) {
      console.error(`Failed to forwardGeocode with error: ${e}`);
    }

    return {
      features,
    };
  },
};
const geocoder = new MaplibreGeocoder(geocoderApi, {
  maplibregl,
  placeholder: 'Search for a place',
  zoom: 12,
  flyTo: {
    zoom: 12,
    padding: cameraPadding(),
    essential: true,
  },
});
document.querySelector('#geocoder').appendChild(geocoder.onAdd(map));

function geolocateAndFindRiver() {
  'use strict';
  function success(position) {
    const latitude = position.coords.latitude;
    const longitude = position.coords.longitude;
    mapPosition(latitude, longitude);
  }

  function error() {
    status.textContent = 'Unable to retrieve your location';
  }

  if (!navigator.geolocation) {
    status.textContent = 'Geolocation is not supported by your browser';
  } else {
    status.textContent = 'Locating…';
    navigator.geolocation.getCurrentPosition(success, error, {
      enableHighAccuracy: true,
    });
  }
}

document
  .querySelector('#find-closest-river')
  .addEventListener('click', geolocateAndFindRiver);
