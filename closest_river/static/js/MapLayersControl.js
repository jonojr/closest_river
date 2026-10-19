// Map control for choosing the background map and switching groups of layers
// on and off.
//
// basemaps: { options: [{ key, label }], selected, onSelect(key) }. The
//   control only reports the choice; the map owns swapping the style.
// groups: [{ key, label, layers: [layerId, ...] }]. Each group's choice is
//   remembered in localStorage and re-applied whenever the style reloads (a
//   basemap or theme change rebuilds every layer). Add this control after
//   registering the 'style.load' handler that creates the layers, so it runs
//   second and finds them in place.
export class MapLayersControl {
  constructor({ basemaps, groups }) {
    this._basemaps = basemaps;
    this._groups = groups;
    this._visible = {};
    for (const group of groups) {
      this._visible[group.key] = this._stored(group.key) ?? true;
    }
    this._apply = this._apply.bind(this);
    this._closeOnOutsideClick = this._closeOnOutsideClick.bind(this);
  }

  onAdd(map) {
    this._map = map;
    this._container = document.createElement('div');
    this._container.className =
      'maplibregl-ctrl maplibregl-ctrl-group layer-control';

    this._button = document.createElement('button');
    this._button.type = 'button';
    this._button.title = 'Map layers';
    this._button.setAttribute('aria-label', 'Map layers');
    this._button.setAttribute('aria-expanded', 'false');
    this._button.innerHTML = '<i class="bi bi-layers" aria-hidden="true"></i>';
    this._button.addEventListener('click', () =>
      this._setOpen(this._panel.hidden),
    );

    this._panel = document.createElement('div');
    this._panel.className = 'layer-control__panel';
    this._panel.hidden = true;
    this._panel.append(
      this._section('Background', this._basemapChoices()),
      this._section(
        'Layers',
        this._groups.map((group) => this._switch(group)),
      ),
    );

    this._container.append(this._button, this._panel);
    this._container.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        this._setOpen(false);
        this._button.focus();
      }
    });
    document.addEventListener('click', this._closeOnOutsideClick);
    map.on('style.load', this._apply);
    return this._container;
  }

  onRemove() {
    document.removeEventListener('click', this._closeOnOutsideClick);
    this._map.off('style.load', this._apply);
    this._container.remove();
    this._map = undefined;
  }

  _section(title, children) {
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'layer-control__section';
    const legend = document.createElement('legend');
    legend.textContent = title;
    fieldset.append(legend, ...children);
    return fieldset;
  }

  _basemapChoices() {
    const { options, selected, onSelect } = this._basemaps;
    return options.map(({ key, label }) => {
      const id = `basemap-${key}`;
      const wrapper = document.createElement('div');
      wrapper.className = 'form-check';

      const input = document.createElement('input');
      input.className = 'form-check-input';
      input.type = 'radio';
      input.name = 'basemap';
      input.id = id;
      input.checked = key === selected;
      input.addEventListener('change', () => onSelect(key));

      const text = document.createElement('label');
      text.className = 'form-check-label';
      text.htmlFor = id;
      text.textContent = label;

      wrapper.append(input, text);
      return wrapper;
    });
  }

  _switch(group) {
    const id = `layer-toggle-${group.key}`;
    const wrapper = document.createElement('div');
    wrapper.className = 'form-check form-switch';

    const input = document.createElement('input');
    input.className = 'form-check-input';
    input.type = 'checkbox';
    input.role = 'switch';
    input.id = id;
    input.checked = this._visible[group.key];
    input.addEventListener('change', () => {
      this._visible[group.key] = input.checked;
      try {
        localStorage.setItem(id, String(input.checked));
      } catch {
        // Storage blocked: the choice just won't persist.
      }
      this._apply();
    });

    const label = document.createElement('label');
    label.className = 'form-check-label';
    label.htmlFor = id;
    label.textContent = group.label;

    wrapper.append(input, label);
    return wrapper;
  }

  _stored(key) {
    try {
      const value = localStorage.getItem(`layer-toggle-${key}`);
      return value === null ? null : value === 'true';
    } catch {
      return null;
    }
  }

  // A layer can belong to several groups (intermittent streams are both
  // streams and intermittent); it shows only while every one of them is on.
  _apply() {
    const visible = {};
    for (const group of this._groups) {
      for (const layer of group.layers) {
        visible[layer] = (visible[layer] ?? true) && this._visible[group.key];
      }
    }
    for (const [layer, show] of Object.entries(visible)) {
      if (this._map.getLayer(layer)) {
        this._map.setLayoutProperty(
          layer,
          'visibility',
          show ? 'visible' : 'none',
        );
      }
    }
  }

  _setOpen(open) {
    this._panel.hidden = !open;
    this._button.setAttribute('aria-expanded', String(open));
  }

  _closeOnOutsideClick(event) {
    if (!this._container.contains(event.target)) {
      this._setOpen(false);
    }
  }
}
