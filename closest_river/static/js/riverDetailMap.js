import maplibregl from 'maplibre-gl';
import { createRiverMap } from './riverMap';

// The info panel floats over the left of the map on wider screens and along
// the bottom on phones; keep the river in the part of the map left visible.
const wideScreen = window.matchMedia('(min-width: 768px)');
function cameraPadding() {
  'use strict';
  return wideScreen.matches
    ? { top: 40, right: 70, bottom: 40, left: 380 }
    : { top: 40, right: 60, bottom: 300, left: 30 };
}

// The surrounding network is faded so the river being viewed stands out.
const { map, flow, setRiver } = createRiverMap({ dimNetwork: true });

const osmId = document.querySelector('#map').dataset.osmId;
fetch(`/rivers/geometry/${osmId}/`)
  .then((response) => response.json())
  .then((data) => {
    const geojson = JSON.parse(data.geometry);
    setRiver(geojson, { intermittent: data.intermittent });
    flow.start();

    // Sections are LineStrings, or MultiLineStrings where a way was split.
    const bounds = new maplibregl.LngLatBounds();
    for (const feature of geojson.features) {
      const { type, coordinates } = feature.geometry;
      const lines = type === 'MultiLineString' ? coordinates : [coordinates];
      for (const line of lines) {
        for (const coord of line) {
          bounds.extend(coord);
        }
      }
    }
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: cameraPadding(), animate: false });
    }
  });
