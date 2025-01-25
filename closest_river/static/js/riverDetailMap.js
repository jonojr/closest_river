import 'maplibre-gl/dist/maplibre-gl.css';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';
import '@mapbox-controls/ruler/src/index.css';

import maplibregl from 'maplibre-gl';
import RulerControl from '@mapbox-controls/ruler';

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

  const layers = map.getStyle().layers;
  // Find the index of the first symbol layer in the map style
  let firstSymbolId;
  for (let i = 0; i < layers.length; i++) {
    if (layers[i].type === 'symbol') {
      firstSymbolId = layers[i].id;
      break;
    }
  }

  const img = document.querySelector('#arrow');
  map.addImage('arrow', img);
  map.addSource('river-sections-data', {
    type: 'vector',
    url: '/rivers/river-sections/tiles.json',
  });
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
        'line-color': 'rgba(105,112,255,0.3)',
        'line-width': 2,
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
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': 150,
        'icon-image': 'arrow', // The arrow image
        'icon-size': 1,
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
        'symbol-spacing': 150,
        'icon-image': 'arrow', // The arrow image
        'icon-size': 1.5,
      },
    },
    firstSymbolId,
  );

  const osm_id = document.querySelector('#osm_id').innerText;
  fetch(`/rivers/geometry/${osm_id}/`)
    .then((response) => response.json())
    .then((data) => {
      const parsed_geojson = JSON.parse(data.geometry);

      map.getSource('river').setData(parsed_geojson);

      const all_coordinates = parsed_geojson.features.reduce((acc, feature) => {
        acc.push(...feature.geometry.coordinates);
        return acc;
      }, []);
      const bounds = all_coordinates.reduce(
        (bounds, coord) => {
          return bounds.extend(coord);
        },
        new maplibregl.LngLatBounds(all_coordinates[0], all_coordinates[0]),
      );

      map.fitBounds(bounds, {
        padding: 40,
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
});

const nav = new maplibregl.NavigationControl({ showCompass: false });
map.addControl(nav, 'top-right');
map.addControl(new RulerControl(), 'top-right');

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
