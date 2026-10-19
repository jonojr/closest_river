import 'maplibre-gl/dist/maplibre-gl.css';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';
import '@mapbox-controls/ruler/src/index.css';

import RulerControl from '@mapbox-controls/ruler';
import maplibregl from 'maplibre-gl';
import MaplibreGeocoder from '@maplibre/maplibre-gl-geocoder';
import { animateFlow, chevronImage } from './flowDirection';

const map = new maplibregl.Map({
  container: 'map', // container id
  style: `https://api.maptiler.com/maps/dataviz/style.json?key=${process.env.MAPTILER_API_KEY}`,
  center: [135.0951931, -26.3911232],
  zoom: 3, // starting zoom
});

// Keep in step with $primary in custom_bootstrap_vars.scss.
const COLOURS = {
  waterway: '#3b82d6',
  selectedRiver: '#0a3a82',
  selectedRiverCasing: '#ffffff',
  selectedRiverFlow: '#8ec5ff',
  lineToRiver: '#334155',
  marker: '#0b63c5',
};

// The finder and result panels float over the map: down the left on wider
// screens, top and bottom on phones. Pad the camera so results land in the
// part of the map that is still visible.
const wideScreen = window.matchMedia('(min-width: 768px)');
function cameraPadding() {
  'use strict';
  return wideScreen.matches
    ? { top: 40, right: 70, bottom: 40, left: 380 }
    : { top: 230, right: 60, bottom: 290, left: 30 };
}

function changeCursorOnHover(map, layerName) {
  'use strict';

  // Change the cursor to a pointer when the mouse is over the places layer.
  map.on('mouseenter', layerName, () => {
    map.getCanvas().style.cursor = 'pointer';
  });

  // Change it back to a pointer when it leaves.
  map.on('mouseleave', layerName, () => {
    map.getCanvas().style.cursor = '';
  });
}

let distanceToClosestRiver = 0;

map.on('load', (e) => {
  'use strict';

  const layers = map.getStyle().layers;
  // Find the index of the first symbol layer in the map style
  let firstSymbolId;
  for (let i = 0; i < layers.length; i++) {
    if (layers[i].type === 'symbol') {
      firstSymbolId = layers[i].id;
      break;
    }
  }

  const networkArrow = chevronImage({
    size: 12,
    fill: COLOURS.waterway,
    outline: '#ffffff',
  });
  map.addImage('network-arrow', networkArrow.image, networkArrow.options);
  const selectedArrow = chevronImage({
    size: 18,
    fill: '#ffffff',
    outline: COLOURS.selectedRiver,
  });
  map.addImage('selected-arrow', selectedArrow.image, selectedArrow.options);
  map.addSource('river-sections-data', {
    type: 'vector',
    url: '/rivers/river-sections/tiles.json',
  });
  // Streams first, so rivers draw on top of the wash.
  //
  // Deliberately lighter and thinner than rivers: at low zoom the stream
  // network is dense enough that drawing it at full weight turns into a solid
  // block of colour. Kept subordinate, it reads as texture and the main river
  // network stays legible against it.
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
        'line-color': COLOURS.waterway,
        'line-opacity': 0.5,
        // Hairline when zoomed out, approaching the river weight as you zoom in.
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4,
          0.5,
          9,
          0.8,
          14,
          1.5,
        ],
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
        'line-color': COLOURS.waterway,
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4,
          1,
          9,
          2,
          14,
          2.5,
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
  map.addSource(`river`, {
    type: 'geojson',
    data: {
      type: 'FeatureCollection',
      features: [], // Start with no lines
    },
  });
  map.addLayer(
    {
      id: 'river-casing',
      type: 'line',
      source: `river`,
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': COLOURS.selectedRiverCasing,
        'line-width': 9,
        'line-opacity': 0.9,
      },
    },
    firstSymbolId,
  );
  map.addLayer(
    {
      id: `river`,
      type: 'line',
      source: `river`,
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': COLOURS.selectedRiver,
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
      source: `river`,
      layout: {
        'line-join': 'round',
      },
      paint: {
        'line-color': COLOURS.selectedRiverFlow,
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

  map.addSource(`line_to_river`, {
    type: 'geojson',
    data: {
      type: 'LineString',
      geometry: {
        type: 'LineString',
        coordinates: [],
      },
    },
  });
  map.addLayer(
    {
      id: `line_to_river`,
      type: 'line',
      source: `line_to_river`,
      layout: {
        'line-cap': 'round',
      },
      paint: {
        'line-color': COLOURS.lineToRiver,
        'line-width': 3,
        'line-dasharray': [0.5, 2],
      },
    },
    firstSymbolId,
  );
});

map.on('click', 'river', (e) => {
  'use strict';
  const description = JSON.parse(e.features[0].properties.tags);

  let html = '<div class="container">';
  for (const key of Object.keys(description)) {
    html += `<div class="row">
            <p class="col-5" style="text-wrap: pretty;">${key}</p><p class="col-7" style="text-wrap: pretty;">${description[key]}</p>
        </div>`;
  }
  html += '</div>';

  new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
});

function showWaterwayPopup(e) {
  'use strict';
  const description = e.features[0].properties;

  // At cached zooms the stream layer is merged into a single geometry to
  // keep tiles small, so individual ways have no id and there is nothing to
  // look up. Zooming in past the cache threshold (see CACHE_BELOW_ZOOM_LEVEL)
  // un-merges them and makes them clickable again.
  if (description.osm_way_id === undefined) {
    return;
  }

  let html = '<div class="container">';

  fetch(`/rivers/river-sections/${description.osm_way_id}/popup_data/`)
    .then((response) => response.json())
    .then((data) => {
      for (const key of Object.keys(data)) {
        const value = data[key];
        html += '<div class="row">';
        html += `<p class="col-5" style="text-wrap: pretty;">${key}</p>`;

        if (value.hasOwnProperty('link')) {
          html += `<a class="col-7" style="text-wrap: pretty;" href="${value.link}" target="_blank">${value.text}</a>`;
        } else {
          html += `<p class="col-7" style="text-wrap: pretty;">${value.text}</p>`;
        }
        html += `</div>`;
      }
      html += '</div>';

      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
}

map.on('click', 'river-sections', showWaterwayPopup);
map.on('click', 'stream-sections', showWaterwayPopup);

map.on('click', 'line_to_river', (e) => {
  'use strict';

  let html = `<div class="container"><p>Distance to the closest river: ${distanceToClosestRiver}km</p></div>`;

  new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
});

const nav = new maplibregl.NavigationControl({ visualizePitch: true });
map.addControl(nav, 'top-right');

map.addControl(new RulerControl(), 'top-right');

const scale = new maplibregl.ScaleControl({
  maxWidth: 80,
  unit: 'metric',
});
map.addControl(scale, 'bottom-right');

changeCursorOnHover(map, 'river');
changeCursorOnHover(map, 'river-sections');
changeCursorOnHover(map, 'stream-sections');
changeCursorOnHover(map, 'line_to_river');

const marker = new maplibregl.Marker({ color: COLOURS.marker });

const riverCard = document.querySelector('#river-card');
let flowAnimating = false;

document.querySelector('#river-card-close').addEventListener('click', () => {
  'use strict';
  riverCard.hidden = true;
});

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
  const status = document.querySelector('#status');

  const name = document.querySelector('#name');
  const wikipedia = document.querySelector('#wikipedia');
  const destination = document.querySelector('#destination');
  const distance = document.querySelector('#distance');

  riverCard.hidden = true;
  status.textContent = 'Finding the nearest river…';

  marker.setLngLat([longitude, latitude]).addTo(map);

  map.flyTo({
    center: [longitude, latitude],
    zoom: 12,
    padding: cameraPadding(),
    essential: true, // this animation is considered essential with respect to prefers-reduced-motion
  });

  fetch(`rivers/closest_river/${latitude}/${longitude}`)
    .then((response) => response.json())
    .then((data) => {
      const feature = data.section.river ? data.river : data.section;

      name.textContent = feature?.name || 'Unnamed waterway';
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

      map.getSource('line_to_river').setData({
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'LineString',
          coordinates: [
            [longitude, latitude],
            [data.closest_point_on_river[0], data.closest_point_on_river[1]],
          ],
        },
      });

      if (data.geometry) {
        map.getSource('river').setData(JSON.parse(data.geometry));
        // Only start animating once there is a river to animate, so the map
        // isn't repainting continuously while it sits idle.
        if (!flowAnimating) {
          animateFlow(map, 'river-flow');
          flowAnimating = true;
        }
      }

      map.fitBounds(
        new maplibregl.LngLatBounds(
          [longitude, latitude],
          [longitude, latitude],
        ).extend(data.closest_point_on_river),
        { padding: cameraPadding(), maxZoom: 13, essential: true },
      );
    })
    .catch(() => {
      status.textContent = "Couldn't find a river near there. Try again?";
    });
}

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
  const status = document.querySelector('#status');

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
