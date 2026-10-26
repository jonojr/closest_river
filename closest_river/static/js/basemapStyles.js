// The background maps, as MapLibre styles built here rather than fetched.
//
// Everything except Satellite's imagery is served by us under /tiles/ (see
// rivers/basemap.py): Protomaps vector tiles, styled with @protomaps/basemaps,
// and for Terrain, Mapterhorn elevation drawn as hillshade and contours in the
// browser. Satellite is MapTiler's imagery with the same labels on top, so it
// is the only thing left using MAPTILER_API_KEY.

import { layers, namedFlavor } from '@protomaps/basemaps';
import mlcontour from 'maplibre-contour';
import * as maplibregl from 'maplibre-gl';
import { PMTiles, Protocol } from 'pmtiles';

const TILES = `${window.location.origin}/tiles`;

// Keep in step with BASEMAP_TERRAIN_MAXZOOM in config/settings/base.py. The
// map overzooms the hillshade and contours past it.
const TERRAIN_MAXZOOM = 11;

const ATTRIBUTION = {
  protomaps:
    '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
  mapterhorn: '<a href="https://mapterhorn.com/attribution">© Mapterhorn</a>',
};

const protocol = new Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

///////////////
// Elevation //
///////////////

// One DEM source feeds both the hillshade and the contours, so each terrain
// tile is fetched and decoded once. maplibre-contour fetches tiles itself and
// knows nothing of the pmtiles:// protocol, so it runs on the main thread
// (worker: false), where its tile fetch can be pointed at the archive.
const terrainArchive = new PMTiles(`${TILES}/terrain.pmtiles`);
const dem = new mlcontour.DemSource({
  url: 'terrain/{z}/{x}/{y}',
  encoding: 'terrarium',
  maxzoom: TERRAIN_MAXZOOM,
  worker: false,
});

// Sea level, for tiles the archive doesn't have (outside a regional extract):
// a flat tile draws no shading and no contours, where an error would be
// logged for every tile.
let flatTile;
function seaLevelTile() {
  if (!flatTile) {
    const canvas = new OffscreenCanvas(512, 512);
    const context = canvas.getContext('2d');
    // Terrarium encodes height as (R * 256 + G + B / 256) - 32768.
    context.fillStyle = 'rgb(128, 0, 0)';
    context.fillRect(0, 0, 512, 512);
    flatTile = canvas.convertToBlob({ type: 'image/png' });
  }
  return flatTile;
}

dem.manager.getTile = async (url, abortController) => {
  const [z, x, y] = url.split('/').slice(-3).map(Number);
  const tile = await terrainArchive.getZxy(z, x, y, abortController.signal);
  if (!tile) {
    return { data: await seaLevelTile() };
  }
  return {
    data: new Blob([tile.data], { type: 'image/webp' }),
    cacheControl: tile.cacheControl,
    expires: tile.expires,
  };
};
dem.setupMaplibre(maplibregl);

const contourTiles = dem.contourProtocolUrl({
  // zoom: [minor, major] interval in metres.
  thresholds: {
    10: [200, 1000],
    11: [100, 500],
    12: [50, 250],
    13: [20, 100],
    15: [10, 50],
  },
  contourLayer: 'contours',
  elevationKey: 'ele',
  levelKey: 'level',
});

const TERRAIN_COLOURS = {
  light: {
    shadow: '#473b24',
    accent: '#5a4a32',
    highlight: '#ffffff',
    exaggeration: 0.35,
    contour: 'rgba(130, 100, 60, 0.4)',
    contourLabel: '#8a6d45',
    peakLabel: '#5c4630',
  },
  dark: {
    shadow: '#000000',
    accent: '#000000',
    highlight: 'rgba(255, 255, 255, 0.12)',
    exaggeration: 0.4,
    contour: 'rgba(205, 185, 150, 0.22)',
    contourLabel: '#a8987c',
    peakLabel: '#cdbb9c',
  },
};

// National parks and reserves, and military land, which Protomaps draws in
// its zoo colour: on most flavours that reads as water.
const AREA_COLOURS = {
  light: {
    parkLabel: '#3f7a3c',
    parkOutline: 'rgba(63, 122, 60, 0.5)',
    militaryFill: 'rgba(130, 110, 80, 0.12)',
    militaryOutline: 'rgba(110, 90, 60, 0.55)',
    residential: 'rgba(200, 180, 150, 0.22)',
    commercial: 'rgba(225, 150, 150, 0.18)',
  },
  dark: {
    parkLabel: '#83b47e',
    parkOutline: 'rgba(131, 180, 126, 0.4)',
    militaryFill: 'rgba(200, 180, 140, 0.07)',
    militaryOutline: 'rgba(200, 180, 140, 0.35)',
    residential: 'rgba(255, 235, 205, 0.05)',
    commercial: 'rgba(255, 170, 170, 0.06)',
  },
};

/////////////
// Flavors //
/////////////

// Place and road names darker than Protomaps', on a halo that actually stands
// out from the land: its halos are the land's own grey.
const LABELS = {
  light: {
    city_label: '#262626',
    city_label_halo: 'rgba(255, 255, 255, 0.9)',
    subplace_label: '#4f4f4f',
    subplace_label_halo: 'rgba(255, 255, 255, 0.9)',
    state_label: '#7a7a7a',
    state_label_halo: 'rgba(255, 255, 255, 0.9)',
    roads_label_major: '#3d3d3d',
    roads_label_major_halo: 'rgba(255, 255, 255, 0.9)',
    roads_label_minor: '#555555',
    roads_label_minor_halo: 'rgba(255, 255, 255, 0.9)',
    address_label: '#6b6b6b',
  },
  dark: {
    city_label: '#ededed',
    city_label_halo: 'rgba(10, 10, 10, 0.85)',
    subplace_label: '#b5b5b5',
    subplace_label_halo: 'rgba(10, 10, 10, 0.85)',
    state_label: '#9a9a9a',
    state_label_halo: 'rgba(10, 10, 10, 0.85)',
    roads_label_major: '#c8c8c8',
    roads_label_major_halo: 'rgba(10, 10, 10, 0.85)',
    roads_label_minor: '#a8a8a8',
    roads_label_minor_halo: 'rgba(10, 10, 10, 0.85)',
    address_label: '#8f8f8f',
  },
};

// Streets: motorways orange and main roads yellow, so the network reads at a
// glance, where Protomaps draws every road white.
const ROAD_COLOURS = {
  light: {
    highway: '#f9b86b',
    highway_casing_early: '#e0954a',
    highway_casing_late: '#e0954a',
    bridges_highway: '#f9b86b',
    bridges_highway_casing: '#e0954a',
    tunnel_highway: '#fbd5a6',
    link: '#fcd39c',
    link_casing: '#e0a868',
    bridges_link: '#fcd39c',
    bridges_link_casing: '#e0a868',
    major: '#fde9a6',
    major_casing_early: '#dcc27a',
    major_casing_late: '#dcc27a',
    bridges_major: '#fde9a6',
    bridges_major_casing: '#dcc27a',
    tunnel_major: '#f8efd0',
  },
  dark: {
    highway: '#8c6a3c',
    highway_casing_early: '#5a4426',
    highway_casing_late: '#5a4426',
    bridges_highway: '#8c6a3c',
    bridges_highway_casing: '#5a4426',
    tunnel_highway: '#5a4a35',
    link: '#735a38',
    link_casing: '#4a3a24',
    bridges_link: '#735a38',
    bridges_link_casing: '#4a3a24',
    major: '#5e5540',
    major_casing_early: '#3e382a',
    major_casing_late: '#3e382a',
    bridges_major: '#5e5540',
    bridges_major_casing: '#3e382a',
    tunnel_major: '#45402f',
  },
};

const STREETS_FLAVORS = {
  light: { ...namedFlavor('light'), ...LABELS.light, ...ROAD_COLOURS.light },
  dark: { ...namedFlavor('dark'), ...LABELS.dark, ...ROAD_COLOURS.dark },
};

// Terrain: the standard flavours with vegetation strong enough to read as
// country rather than a street map's park tint.
const TERRAIN_FLAVORS = {
  light: {
    ...namedFlavor('light'),
    ...LABELS.light,
    earth: '#ece8df',
    park_a: '#cfe0c4',
    park_b: '#b3d4a3',
    wood_a: '#c7dfbd',
    wood_b: '#a9cf98',
    scrub_a: '#dde5c8',
    scrub_b: '#cbd9a9',
    landcover: {
      ...namedFlavor('light').landcover,
      forest: 'rgba(181, 214, 170, 1)',
      scrub: 'rgba(219, 229, 192, 1)',
      grassland: 'rgba(221, 234, 199, 1)',
      farmland: 'rgba(230, 234, 205, 1)',
      barren: 'rgba(240, 229, 204, 1)',
    },
  },
  dark: {
    ...namedFlavor('dark'),
    ...LABELS.dark,
    park_a: '#1d2a20',
    park_b: '#203124',
    wood_a: '#1d2a20',
    wood_b: '#22332a',
    landcover: {
      ...namedFlavor('dark').landcover,
      forest: 'rgba(27, 45, 33, 1)',
      scrub: 'rgba(36, 41, 30, 1)',
      grassland: 'rgba(31, 42, 30, 1)',
    },
  },
};

// Minimal: Protomaps' white and black, with water tinted just enough to read
// as water under the rivers rather than as a grey hole, and parks just enough
// to find.
const MINIMAL_FLAVORS = {
  light: {
    ...namedFlavor('white'),
    ...LABELS.light,
    water: '#dce6ef',
    park_a: '#eef4eb',
    park_b: '#e3eedf',
    wood_a: '#eef4eb',
    wood_b: '#e3eedf',
    boundaries: '#c9b3bf',
  },
  dark: {
    ...namedFlavor('black'),
    ...LABELS.dark,
    water: '#1a2330',
    park_a: '#18201a',
    park_b: '#1b251d',
    wood_a: '#18201a',
    wood_b: '#1b251d',
    boundaries: '#5a4a53',
  },
};

// Satellite's labels: the dark flavour's are grey, which disappears into
// imagery, so white with a dark halo instead.
const IMAGERY_LABEL = 'rgba(255, 255, 255, 0.95)';
const IMAGERY_HALO = 'rgba(0, 0, 0, 0.75)';
const SATELLITE_FLAVOR = {
  ...namedFlavor('dark'),
  city_label: IMAGERY_LABEL,
  city_label_halo: IMAGERY_HALO,
  subplace_label: IMAGERY_LABEL,
  subplace_label_halo: IMAGERY_HALO,
  state_label: IMAGERY_LABEL,
  state_label_halo: IMAGERY_HALO,
  roads_label_major: IMAGERY_LABEL,
  roads_label_major_halo: IMAGERY_HALO,
  roads_label_minor: IMAGERY_LABEL,
  roads_label_minor_halo: IMAGERY_HALO,
  address_label: IMAGERY_LABEL,
  address_label_halo: IMAGERY_HALO,
  // Lake and sea names.
  ocean_label: '#cfe6ff',
};

////////////
// Styles //
////////////

const NAME = ['coalesce', ['get', 'name:en'], ['get', 'name']];

// The basemap's own river names, in our palette and from much further out
// than Protomaps' default of zoom 13, which zoomed out labels nothing at all.
// Rivers and canals (plenty of urban creeks are concrete channels tagged as
// canals) from zoom 8; streams and drains once there is room for them.
function riverNames(styleLayers, colours) {
  const index = styleLayers.findIndex(
    (layer) => layer.id === 'water_waterway_label',
  );
  if (index === -1) {
    return styleLayers;
  }
  const base = styleLayers[index];
  const styled = (id, kinds, minzoom) => ({
    ...base,
    id,
    minzoom,
    filter: ['in', ['get', 'kind'], ['literal', kinds]],
    layout: {
      ...base.layout,
      'symbol-spacing': 400,
      // Small, tight text that may follow sharp bends gets far more names
      // placed on winding rivers.
      'text-size': ['interpolate', ['linear'], ['zoom'], 11, 11, 15, 15],
      'text-letter-spacing': [
        'interpolate',
        ['linear'],
        ['zoom'],
        12,
        0,
        14,
        0.05,
      ],
      'text-max-angle': 90,
    },
    paint: {
      'text-color': colours.label,
      'text-halo-color': colours.labelHalo,
      'text-halo-width': 1.75,
    },
  });
  const rest = [
    ...styleLayers.slice(0, index),
    ...styleLayers.slice(index + 1),
  ];
  // Just under the place names: a river's name wins against roads and points
  // of interest, but never knocks out the name of the town it runs through.
  insertBefore(
    rest,
    'places_subplace',
    styled('water_waterway_label_minor', ['stream', 'drain', 'ditch'], 13),
    styled('water_waterway_label', ['river', 'canal'], 8),
  );
  return rest;
}

const findLayer = (styleLayers, id) =>
  styleLayers.find((layer) => layer.id === id);

const removeLayers = (styleLayers, ...ids) =>
  styleLayers.filter((layer) => !ids.includes(layer.id));

const setLayout = (layer, layout) => {
  if (layer) {
    layer.layout = { ...layer.layout, ...layout };
  }
};

const setPaint = (layer, paint) => {
  if (layer) {
    layer.paint = { ...layer.paint, ...paint };
  }
};

// Bigger, bolder place and road names with a wider halo, and towns packed
// closer together: Protomaps' spacing dropped most of the towns its tiles
// carry once zoomed out.
function readableLabels(styleLayers) {
  // population_rank runs 0-15; Melbourne is 12, a country town 2-6.
  const byPopulation = (small, town, city, bigCity) => [
    'step',
    ['get', 'population_rank'],
    small,
    6,
    town,
    9,
    city,
    11,
    bigCity,
  ];
  // Towns as a dot with the name beside it, above where there is room, until
  // zoomed in far enough that the town is an area rather than a point.
  // Protomaps drops the dot and centres the name from zoom 8.
  const DOTS_UNTIL = 13;
  setLayout(findLayer(styleLayers, 'places_locality'), {
    'icon-image': [
      'step',
      ['zoom'],
      ['case', ['==', ['get', 'capital'], 'yes'], 'capital', 'townspot'],
      DOTS_UNTIL,
      '',
    ],
    'icon-size': 0.7,
    'text-variable-anchor': [
      'step',
      ['zoom'],
      ['literal', ['bottom', 'top', 'left', 'right']],
      DOTS_UNTIL,
      ['literal', ['center']],
    ],
    'text-radial-offset': 0.45,
    'text-font': ['Noto Sans Medium'],
    'text-size': [
      'interpolate',
      ['linear'],
      ['zoom'],
      4,
      byPopulation(10, 10, 11, 14),
      8,
      byPopulation(12, 13, 15, 17),
      11,
      byPopulation(13, 15, 17, 20),
      15,
      byPopulation(15, 17, 19, 22),
    ],
    'text-padding': ['interpolate', ['linear'], ['zoom'], 5, 2, 8, 3, 12, 6],
  });
  setLayout(findLayer(styleLayers, 'places_subplace'), {
    'text-size': [
      'interpolate',
      ['exponential', 1.2],
      ['zoom'],
      11,
      10,
      14,
      14,
      18,
      22,
    ],
    'text-letter-spacing': 0.08,
  });
  setLayout(findLayer(styleLayers, 'roads_labels_major'), {
    'text-size': ['interpolate', ['linear'], ['zoom'], 11, 11, 14, 13, 18, 15],
  });
  setLayout(findLayer(styleLayers, 'roads_labels_minor'), {
    'text-size': ['interpolate', ['linear'], ['zoom'], 15, 12, 18, 14],
  });
  setLayout(findLayer(styleLayers, 'earth_label_islands'), {
    'text-size': ['interpolate', ['linear'], ['zoom'], 8, 11, 13, 13],
  });
  for (const id of [
    'places_locality',
    'places_subplace',
    'places_region',
    'roads_labels_major',
    'roads_labels_minor',
    'earth_label_islands',
  ]) {
    setPaint(findLayer(styleLayers, id), { 'text-halo-width': 1.5 });
  }
}

// Towns as a warm tint, and their shops and offices a faint pink, so built-up
// land stands out from farmland. Under the parks, which often sit inside it.
function builtUpLand(styleLayers, area) {
  const fill = (id, kinds, colour) => ({
    id,
    type: 'fill',
    source: 'protomaps',
    'source-layer': 'landuse',
    filter: ['in', ['get', 'kind'], ['literal', kinds]],
    paint: {
      'fill-color': colour,
      'fill-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0, 10, 1],
    },
  });
  insertBefore(
    styleLayers,
    'landuse_park',
    fill('landuse_residential', ['residential'], area.residential),
    fill('landuse_commercial', ['commercial', 'retail'], area.commercial),
  );
}

// Military land gets its own muted fill and a dashed boundary, in place of
// the zoo colour Protomaps' park layer gives it.
const MILITARY_KINDS = ['military', 'naval_base', 'airfield'];

function militaryLand(styleLayers, area) {
  const parks = findLayer(styleLayers, 'landuse_park');
  if (!parks) {
    return;
  }
  // A legacy ['in', 'kind', ...kinds] filter.
  parks.filter = parks.filter.filter((term) => !MILITARY_KINDS.includes(term));
  const filter = ['in', ['get', 'kind'], ['literal', MILITARY_KINDS]];
  const index = styleLayers.indexOf(parks) + 1;
  styleLayers.splice(
    index,
    0,
    {
      id: 'landuse_military',
      type: 'fill',
      source: 'protomaps',
      'source-layer': 'landuse',
      filter,
      paint: { 'fill-color': area.militaryFill },
    },
    {
      id: 'landuse_military_outline',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'landuse',
      minzoom: 9,
      filter,
      paint: {
        'line-color': area.militaryOutline,
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.75, 14, 1.5],
        'line-dasharray': [3, 2],
      },
    },
  );
}

// National parks and reserves: an outline above the land fills, and their
// names. Protomaps carries the name points only to about zoom 12, and its
// own style names none of them.
const PARK_KINDS = ['national_park', 'nature_reserve', 'protected_area'];

function parks(styleLayers, area, halo) {
  insertBefore(styleLayers, 'water', {
    id: 'park-outlines',
    type: 'line',
    source: 'protomaps',
    'source-layer': 'landuse',
    minzoom: 8,
    filter: ['in', ['get', 'kind'], ['literal', PARK_KINDS]],
    paint: {
      'line-color': area.parkOutline,
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.5, 14, 1.5],
    },
  });
  insertBefore(styleLayers, 'places_subplace', {
    id: 'park-labels',
    type: 'symbol',
    source: 'protomaps',
    'source-layer': 'pois',
    minzoom: 8,
    filter: [
      'all',
      ['in', ['get', 'kind'], ['literal', PARK_KINDS]],
      // Protomaps' own fade-in zoom per feature, by area: big parks first.
      ['>=', ['zoom'], ['get', 'min_zoom']],
    ],
    layout: {
      'text-field': NAME,
      'text-font': ['Noto Sans Italic'],
      'text-size': 11,
      'text-max-width': 8,
      'text-letter-spacing': 0.03,
    },
    paint: {
      'text-color': area.parkLabel,
      'text-halo-color': halo,
      'text-halo-width': 1.25,
    },
  });
}

// Ferry routes, which Protomaps' tiles carry but its style doesn't draw.
function ferries(styleLayers, colour) {
  insertBefore(styleLayers, 'roads_tunnels_other_casing', {
    id: 'roads_ferry',
    type: 'line',
    source: 'protomaps',
    'source-layer': 'roads',
    filter: ['==', ['get', 'kind'], 'ferry'],
    paint: {
      'line-color': colour,
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.75, 14, 1.5],
      'line-dasharray': [3, 2],
    },
  });
}

// Airports as places in their own right, named with their code, in place of
// a small icon among the shops. The tiles carry them from zoom 11.
function airports(styleLayers, colours) {
  const pois = findLayer(styleLayers, 'pois');
  if (pois) {
    pois.filter = [...pois.filter, ['!=', ['get', 'kind'], 'aerodrome']];
  }
  insertBefore(styleLayers, 'places_subplace', {
    id: 'airports',
    type: 'symbol',
    source: 'protomaps',
    'source-layer': 'pois',
    filter: ['all', ['==', ['get', 'kind'], 'aerodrome'], ['has', 'iata']],
    layout: {
      'icon-image': 'aerodrome',
      'text-field': [
        'format',
        NAME,
        {},
        '\n',
        {},
        ['get', 'iata'],
        { 'font-scale': 0.85 },
      ],
      'text-font': ['Noto Sans Medium'],
      'text-size': 12,
      'text-anchor': 'top',
      'text-offset': [0, 0.9],
      'text-max-width': 9,
      'symbol-sort-key': [
        'match',
        ['get', 'kind_detail'],
        'international',
        0,
        1,
      ],
    },
    paint: {
      'text-color': colours.label,
      'text-halo-color': colours.halo,
      'text-halo-width': 1.5,
    },
  });
}

// Main roads wider from further out, so the network shows at city scale. The
// casings take the same width as their gap, so they still edge the road.
function strongerRoads(styleLayers) {
  const width = (...stops) => [
    'interpolate',
    ['exponential', 1.6],
    ['zoom'],
    ...stops,
  ];
  const highway = width(3, 0, 6, 1.2, 12, 2.4, 15, 5, 18, 15);
  const major = width(6, 0, 8, 0.8, 12, 2, 15, 3.5, 18, 13);
  setPaint(findLayer(styleLayers, 'roads_highway'), { 'line-width': highway });
  setPaint(findLayer(styleLayers, 'roads_major'), { 'line-width': major });
  for (const id of [
    'roads_highway_casing_early',
    'roads_highway_casing_late',
  ]) {
    setPaint(findLayer(styleLayers, id), { 'line-gap-width': highway });
  }
  for (const id of ['roads_major_casing_early', 'roads_major_casing_late']) {
    setPaint(findLayer(styleLayers, id), { 'line-gap-width': major });
  }
}

// Satellite: roads as translucent lines over the imagery, and state and
// council borders, under the labels.
function imageryLines() {
  const line = (id, sourceLayer, filter, colour, width, options = {}) => ({
    id,
    type: 'line',
    source: 'protomaps',
    'source-layer': sourceLayer,
    minzoom: options.minzoom ?? 0,
    filter,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': colour,
      'line-width': ['interpolate', ['exponential', 1.6], ['zoom'], ...width],
      ...(options.dashes ? { 'line-dasharray': options.dashes } : {}),
    },
  });
  const road = (...kinds) => ['in', ['get', 'kind'], ['literal', kinds]];
  return [
    // Minor roads and paths only once they stop being a solid wash.
    line(
      'imagery-paths',
      'roads',
      road('path'),
      'rgba(255, 255, 255, 0.3)',
      [14, 0.5, 18, 2],
      { minzoom: 14, dashes: [2, 2] },
    ),
    line(
      'imagery-roads-minor',
      'roads',
      road('minor_road'),
      'rgba(255, 255, 255, 0.35)',
      [11, 0.3, 14, 1, 18, 5],
      { minzoom: 11 },
    ),
    line(
      'imagery-roads-major',
      'roads',
      road('major_road'),
      'rgba(255, 255, 255, 0.55)',
      [6, 0.4, 10, 0.9, 14, 2.5, 18, 9],
    ),
    line(
      'imagery-roads-highway',
      'roads',
      road('highway'),
      'rgba(255, 205, 110, 0.75)',
      [5, 0.6, 10, 1.4, 14, 3.5, 18, 12],
    ),
    // kind_detail is the admin level: 2 country, 4 state, 6 council.
    line(
      'imagery-borders-council',
      'boundaries',
      ['>', ['get', 'kind_detail'], 4],
      'rgba(255, 255, 255, 0.35)',
      [8, 0.5, 14, 1],
      { minzoom: 8, dashes: [3, 2] },
    ),
    line(
      'imagery-borders-state',
      'boundaries',
      ['<=', ['get', 'kind_detail'], 4],
      'rgba(255, 255, 255, 0.75)',
      [3, 0.6, 8, 1.2, 14, 2],
      { dashes: [4, 2] },
    ),
  ];
}

function baseStyle(spriteName, sources, styleLayers) {
  return {
    version: 8,
    glyphs: `${TILES}/fonts/{fontstack}/{range}.pbf`,
    sprite: `${TILES}/sprites/v4/${spriteName}`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${TILES}/basemap.pmtiles`,
        attribution: ATTRIBUTION.protomaps,
      },
      ...sources,
    },
    layers: styleLayers,
  };
}

function protomapsLayers(flavor, colours, options = {}) {
  const styleLayers = riverNames(
    layers('protomaps', flavor, { lang: 'en', ...options }),
    colours,
  );
  readableLabels(styleLayers);
  return styleLayers;
}

function streetsStyle(theme, colours) {
  const flavor = STREETS_FLAVORS[theme];
  const area = AREA_COLOURS[theme];
  const styleLayers = protomapsLayers(flavor, colours);
  strongerRoads(styleLayers);
  builtUpLand(styleLayers, area);
  militaryLand(styleLayers, area);
  parks(styleLayers, area, flavor.earth);
  ferries(styleLayers, colours.label);
  airports(styleLayers, {
    label: flavor.city_label,
    halo: flavor.city_label_halo,
  });
  return baseStyle(theme, {}, styleLayers);
}

// Minimal: places, the road network and water, and little else. No shields,
// shops or one-way arrows, and road names only once zoomed in.
function minimalStyle(theme, colours) {
  const styleLayers = removeLayers(
    protomapsLayers(MINIMAL_FLAVORS[theme], colours),
    'roads_shields',
    'pois',
    'roads_oneway',
    'address_label',
  );
  findLayer(styleLayers, 'roads_labels_major').minzoom = 13;
  militaryLand(styleLayers, AREA_COLOURS[theme]);
  return baseStyle(theme === 'dark' ? 'black' : 'white', {}, styleLayers);
}

function insertBefore(styleLayers, beforeId, ...newLayers) {
  const index = styleLayers.findIndex((layer) => layer.id === beforeId);
  styleLayers.splice(
    index === -1 ? styleLayers.length : index,
    0,
    ...newLayers,
  );
}

function terrainStyle(theme, colours) {
  const terrain = TERRAIN_COLOURS[theme];
  const styleLayers = protomapsLayers(TERRAIN_FLAVORS[theme], colours);

  // Shading over the land and vegetation fills, under water and roads.
  insertBefore(styleLayers, 'water', {
    id: 'hillshade',
    type: 'hillshade',
    source: 'terrain',
    paint: {
      'hillshade-shadow-color': terrain.shadow,
      'hillshade-accent-color': terrain.accent,
      'hillshade-highlight-color': terrain.highlight,
      'hillshade-exaggeration': terrain.exaggeration,
    },
  });
  // Contour lines under the roads. Their labels go first in the trailing run
  // of symbol layers: lowest priority among the labels, and still above the
  // rivers, which riverMap.js slots in just before that run.
  insertBefore(styleLayers, 'roads_tunnels_other_casing', {
    id: 'contours',
    type: 'line',
    source: 'contours',
    'source-layer': 'contours',
    paint: {
      'line-color': terrain.contour,
      // level 1 is the major interval.
      'line-width': ['match', ['get', 'level'], 1, 1, 0.5],
    },
  });
  const firstSymbol = styleLayers.find((layer) => layer.type === 'symbol');
  insertBefore(styleLayers, firstSymbol?.id, {
    id: 'contour-labels',
    type: 'symbol',
    source: 'contours',
    'source-layer': 'contours',
    filter: ['>', ['get', 'level'], 0],
    layout: {
      'symbol-placement': 'line',
      'text-size': 10,
      'text-field': ['concat', ['number-format', ['get', 'ele'], {}], ' m'],
      'text-font': ['Noto Sans Regular'],
    },
    paint: {
      'text-color': terrain.contourLabel,
      'text-halo-color': TERRAIN_FLAVORS[theme].earth,
      'text-halo-width': 1,
    },
  });

  const halo = TERRAIN_FLAVORS[theme].earth;
  const area = AREA_COLOURS[theme];
  builtUpLand(styleLayers, area);
  militaryLand(styleLayers, area);
  parks(styleLayers, area, halo);

  // Peaks with their heights: Protomaps carries them, but its style shows
  // them only from zoom 15 and without heights. Above the roads' labels,
  // below the places'.
  const pois = styleLayers.find((layer) => layer.id === 'pois');
  if (pois) {
    pois.filter = [...pois.filter, ['!=', ['get', 'kind'], 'peak']];
  }
  insertBefore(styleLayers, 'places_subplace', {
    // Peaks only start in the zoom 13 tiles.
    id: 'peaks',
    type: 'symbol',
    source: 'protomaps',
    'source-layer': 'pois',
    minzoom: 13,
    filter: ['==', ['get', 'kind'], 'peak'],
    layout: {
      'icon-image': 'peak',
      'text-field': [
        'case',
        ['has', 'elevation'],
        [
          'format',
          NAME,
          {},
          '\n',
          {},
          ['concat', ['number-format', ['get', 'elevation'], {}], ' m'],
          { 'font-scale': 0.85 },
        ],
        NAME,
      ],
      'text-font': ['Noto Sans Regular'],
      'text-size': 11,
      'text-anchor': 'top',
      'text-offset': [0, 0.7],
      'text-max-width': 8,
      // Higher peaks win when labels collide.
      'symbol-sort-key': ['-', ['coalesce', ['get', 'elevation'], 0]],
    },
    paint: {
      'text-color': terrain.peakLabel,
      'text-halo-color': halo,
      'text-halo-width': 1.25,
    },
  });

  return baseStyle(
    theme,
    {
      terrain: {
        type: 'raster-dem',
        tiles: [dem.sharedDemProtocolUrl],
        encoding: 'terrarium',
        tileSize: 512,
        maxzoom: TERRAIN_MAXZOOM,
        attribution: ATTRIBUTION.mapterhorn,
      },
      contours: {
        type: 'vector',
        tiles: [contourTiles],
        maxzoom: 15,
      },
    },
    styleLayers,
  );
}

// Imagery has no light or dark: it gets the dark flavour's labels, which read
// on photographs, over our own faint road lines and borders.
function satelliteStyle(colours) {
  return baseStyle(
    'dark',
    {
      satellite: {
        type: 'raster',
        url: `https://api.maptiler.com/tiles/satellite-v2/tiles.json?key=${process.env.MAPTILER_API_KEY}`,
        tileSize: 512,
      },
    },
    [
      { id: 'satellite', type: 'raster', source: 'satellite' },
      ...imageryLines(),
      ...protomapsLayers(SATELLITE_FLAVOR, colours, { labelsOnly: true }),
    ],
  );
}

// In the order the layers control lists them. style(theme, colours) gives the
// style for 'light' or 'dark', with the river palette for the label colours.
// tone says which river palette reads against the map; it follows the theme
// unless the map is the same either way.
export const BASEMAPS = {
  terrain: { label: 'Terrain', style: terrainStyle },
  // TEMPORARY: MapTiler's Terrain beside ours, for comparison. Remove before
  // merging. A saved choice of it falls back to the default once it's gone.
  terrainMaptiler: {
    label: 'Terrain (MapTiler)',
    style: (theme) =>
      `https://api.maptiler.com/maps/${theme === 'dark' ? 'outdoor-v2-dark' : 'outdoor-v2'}/style.json?key=${process.env.MAPTILER_API_KEY}`,
  },
  streets: { label: 'Streets', style: streetsStyle },
  satellite: {
    label: 'Satellite',
    style: (theme, colours) => satelliteStyle(colours),
    tone: 'dark',
  },
  minimal: { label: 'Minimal', style: minimalStyle },
};
