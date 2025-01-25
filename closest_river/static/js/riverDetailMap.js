import 'maplibre-gl/dist/maplibre-gl.css';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';

import maplibregl from 'maplibre-gl';
import MaplibreGeocoder from '@maplibre/maplibre-gl-geocoder';
import { SearchButtonControl } from './SearchButtonControl';

const map = new maplibregl.Map({
  container: 'map', // container id
  style: `https://api.maptiler.com/maps/streets-v2/style.json?key=${process.env.MAPTILER_API_KEY}`,
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
    minzoom: 7,
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
      'line-color': 'rgba(105,112,255,0.3)',
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

  const osm_id = document.querySelector('#osm_id').innerText;
  console.log(osm_id);
  fetch(`/rivers/geometry/${osm_id}/`)
    .then((response) => response.json())
    .then((data) => {
      const parsed_geojson = JSON.parse(data.geometry);

      map.getSource('river').setData(parsed_geojson);

      const all_coordinates = parsed_geojson.features.reduce((acc, feature) => {
        acc.push(...feature.geometry.coordinates);
        return acc;
      }, []);

      console.log(all_coordinates);
      const bounds = all_coordinates.reduce(
        (bounds, coord) => {
          return bounds.extend(coord);
        },
        new maplibregl.LngLatBounds(all_coordinates[0], all_coordinates[0]),
      );

      map.fitBounds(bounds, {
        padding: 30,
      });
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

const nav = new maplibregl.NavigationControl({ showCompass: false });
map.addControl(nav, 'top-right');

const scale = new maplibregl.ScaleControl({
  maxWidth: 80,
  unit: 'metric',
});
map.addControl(scale);

const compassControl = new maplibregl.NavigationControl({
  showCompass: true,
  showZoom: false,
});
map.addControl(compassControl, 'bottom-left');

changeCursorOnHover(map, 'river');
changeCursorOnHover(map, 'river-sections');
