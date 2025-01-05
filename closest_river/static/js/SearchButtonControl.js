export class SearchButtonControl {
  onAdd(map) {
    this._map = map;
    this._container = document.createElement('div');
    this._container.className = 'maplibregl-ctrl';
    this._button = document.createElement('button');
    this._button.setAttribute('id', 'find-closest-river');
    this._button.setAttribute('class', 'btn btn-primary find-closest-river');
    this._button.textContent = 'Find my closest river';

    this._button.insertAdjacentHTML(
      'afterbegin',
      '<i class="bi bi-search me-1"></i>',
    );
    this._container.appendChild(this._button);
    return this._container;
  }

  onRemove() {
    this._container.parentNode.removeChild(this._container);
    this._map = undefined;
  }
}
