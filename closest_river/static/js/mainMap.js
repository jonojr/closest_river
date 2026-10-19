import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';

import maplibregl from 'maplibre-gl';
import MaplibreGeocoder from '@maplibre/maplibre-gl-geocoder';
import { EMPTY, createRiverMap, popupNote } from './riverMap';

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

// The dotted line from the searched point to the river. Kept outside the map
// so it survives a basemap swap, like the highlighted river.
let lineToRiver = EMPTY;
let distanceToClosestRiver = 0;

const { map, popup, flow, setRiver } = createRiverMap({
  addExtraLayers(map, colours, beforeId) {
    map.addSource('line_to_river', { type: 'geojson', data: lineToRiver });
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
      beforeId,
    );
  },
  extraPopups: {
    line_to_river: () =>
      popupNote(`Distance to the closest river: ${distanceToClosestRiver} km`),
  },
});

const marker = new maplibregl.Marker({ color: '#0b63c5' });

/////////////////
// Find nearest //
/////////////////

const riverCard = document.querySelector('#river-card');
const status = document.querySelector('#status');
const anyWaterway = document.querySelector('#any-waterway');

let lastPosition = null;

function setSelection({ river, line }) {
  setRiver(river);
  lineToRiver = line;
  map.getSource('line_to_river')?.setData(line);
}

// Closing the result card un-selects everything it was describing.
function clearSelection() {
  'use strict';
  riverCard.hidden = true;
  setSelection({ river: EMPTY, line: EMPTY });
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
        line: {
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
