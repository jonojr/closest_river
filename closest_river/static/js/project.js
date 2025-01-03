import '../sass/project.scss';
import 'maplibre-gl/dist/maplibre-gl.css';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';

import maplibregl from 'maplibre-gl';
import MaplibreGeocoder from '@maplibre/maplibre-gl-geocoder';

const map = new maplibregl.Map({
  container: 'map', // container id
  style: 'https://api.maptiler.com/maps/streets/style.json?key=API_KEY_HERE',
  center: [135.0951931, -26.3911232],
  zoom: 3, // starting zoom
});

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

  const img = document.querySelector('#arrow');
  map.addImage('arrow', img);
  map.addSource('river-sections-data', {
    type: 'vector',
    url: '/rivers/river-sections/tiles.json',
  });
  map.addLayer({
    id: 'river-sections',
    type: 'line',
    source: 'river-sections-data',
    'source-layer': 'river-sections',
    layout: {
      'line-join': 'round',
      'line-cap': 'round',
    },
    paint: {
      'line-color': '#6970ff',
      'line-width': 2,
    },
  });
  map.addLayer({
    id: 'river-section-arrows',
    type: 'symbol',
    source: 'river-sections-data',
    'source-layer': 'river-sections',
    layout: {
      'symbol-placement': 'line',
      'symbol-spacing': 150,
      'icon-image': 'arrow', // The arrow image
      'icon-size': 1,
    },
  });
  map.addSource(`river`, {
    type: 'geojson',
    data: {
      type: 'FeatureCollection',
      features: [], // Start with no lines
    },
  });
  map.addLayer({
    id: `river`,
    type: 'line',
    source: `river`,
    layout: {
      'line-join': 'round',
      'line-cap': 'round',
    },
    paint: {
      'line-color': 'rgba(12,35,182,0.91)',
      'line-width': 5,
    },
  });
  map.addLayer({
    id: 'arrows',
    type: 'symbol',
    source: 'river',
    layout: {
      'symbol-placement': 'line',
      'symbol-spacing': 150,
      'icon-image': 'arrow', // The arrow image
      'icon-size': 1.5,
    },
  });

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
  map.addLayer({
    id: `line_to_river`,
    type: 'line',
    source: `line_to_river`,
    paint: {
      'line-color': 'rgba(47,47,48,0.91)',
      'line-width': 5,
      'line-dasharray': [2, 1],
    },
  });
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

map.on('click', 'river-sections', (e) => {
  'use strict';
  const description = e.features[0].properties;

  let html = '<div class="container">';
  for (const key of Object.keys(description)) {
    html += `<div class="row">
            <p class="col-5" style="text-wrap: pretty;">${key}</p><p class="col-7" style="text-wrap: pretty;">${description[key]}</p>
        </div>`;
  }
  html += '</div>';

  new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
});

map.on('click', 'line_to_river', (e) => {
  'use strict';

  let html = `<div class="container"><p>Distance to the closest river: ${distanceToClosestRiver}km</p></div>`;

  new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
});

const nav = new maplibregl.NavigationControl({ showCompass: true });
map.addControl(nav, 'top-right');
const scale = new maplibregl.ScaleControl({
  maxWidth: 80,
  unit: 'metric',
});
map.addControl(scale);

changeCursorOnHover(map, 'river');
changeCursorOnHover(map, 'river-sections');
changeCursorOnHover(map, 'line_to_river');

const marker = new maplibregl.Marker();

function mapPosition(latitude, longitude) {
  'use strict';
  const status = document.querySelector('#status');

  const name = document.querySelector('#name');
  const wikipedia = document.querySelector('#wikipedia');
  const destination = document.querySelector('#destination');
  const distance = document.querySelector('#distance');

  name.textContent = '';
  wikipedia.textContent = '';
  destination.textContent = '';
  status.textContent = '';
  distance.textContent = '';

  marker.setLngLat([longitude, latitude]).addTo(map);

  map.flyTo({
    center: [longitude, latitude],
    zoom: 12,
    essential: true, // this animation is considered essential with respect to prefers-reduced-motion
  });

  fetch(`rivers/closest_river/${latitude}/${longitude}`)
    .then((response) => response.json())
    .then((data) => {
      // result.textContent = JSON.stringify(data, null, 4);
      if (data.section.river) {
        name.textContent = data.river.name;
        if (data.river.destination) {
          destination.textContent = data.river.destination;
        }

        if (data.river.wikipedia) {
          wikipedia.textContent = data.river.wikipedia;
          wikipedia.href = `https://en.wikipedia.org/wiki/${data.river.wikipedia}`;
        }
      } else {
        name.textContent = data.section?.name;
        destination.textContent = data.section?.destination;
        wikipedia.textContent = data.section?.wikipedia;
        wikipedia.href = `https://en.wikipedia.org/wiki/${data.section?.wikipedia}`;
      }
      distance.textContent = `${data.distance}km`;

      distanceToClosestRiver = data.distance;

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
      }
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
map.addControl(
  new MaplibreGeocoder(geocoderApi, {
    maplibregl,
    zoom: 12,
    flyTo: {
      zoom: 12,
      essential: true,
    },
  }),
  'top-left',
);

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
