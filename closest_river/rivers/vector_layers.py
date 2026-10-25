"""Vector tile layers for waterways.

Two layers, so the map can draw a hierarchy: rivers heavier and clickable,
streams as a lighter textured wash.

The hard part is the wash. Australia has ~640k stream ways, and rendering them
naively at low zoom produces multi-megabyte tiles. Rather than throwing
features away — which would destroy the dendritic texture that makes the map
worth looking at — two things do the work:

* **Snap to a sub-pixel grid.** This is what makes it affordable: it collapses
  vertices, and it makes coordinates repeat, which compresses extremely well.
* **Merge touching lines** into one geometry. OSM chops a single creek into
  many ways; merging is geometry-preserving (it concatenates, it never
  discards) and removes the per-feature protobuf overhead, which dominates.

Two things that sound plausible but measured badly:

* **Douglas-Peucker instead of snapping.** It avoids the lattice, but keeps
  full coordinate precision, so nothing repeats and gzip has nothing to work
  with — 1 MB against 151 KB for the same zoom 4 tile.
* **A small tile extent.** Once snapping is doing the generalising, extent
  makes almost no difference to size (512 and 4096 measured within 2%), and a
  small one just adds a second, coarser quantisation on top of the first —
  visible stair-stepping for no saving.

Measured worst case (the Victorian Alps, the densest network in the country),
both layers gzipped: 28 KB at z0, 271 KB at z4 (the peak), 90 KB at z6, 103 KB
at z8, 52 KB at z10. Rendering the same z8 tile naively was 1.4 MB.
"""

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from vectortiles import VectorLayer

from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL
from closest_river.rivers.tile_cache import TILE_CACHE_TIMEOUT_SECS
from closest_river.rivers.tile_cache import get_data_version
from closest_river.rivers.tile_cache import tile_cache_key

EARTH_CIRCUMFERENCE_M = 40075016.686
# MapLibre renders a vector tile across 512 CSS pixels, not 256. Getting this
# wrong makes every grid below twice as coarse as intended, which is visible as
# right-angled staircases in the stream network.
TILE_CSS_PIXELS = 512

# Snapping is what makes the wash affordable — it collapses vertices and makes
# coordinates repeat, which gzip loves. But it moves vertices onto a
# rectangular lattice, so too coarse a grid shows up as visible right angles.
#
# Half a CSS pixel is one device pixel on a retina screen, which is as fine as
# anything can be seen.
SNAP_CSS_PIXELS = 0.5
# ...except at the widest zooms, where a tile spans a continent and the fine
# grid triples its size. Individual channels are not separable there — the
# network reads as a single texture — so the lattice does not show.
COARSE_SNAP_CSS_PIXELS = 1.0
COARSE_SNAP_BELOW_ZOOM = 4

# One extent everywhere. Measured, a larger extent costs almost nothing once
# snapping is doing the generalising, and a small one only adds a second,
# coarser quantisation on top — more visible stair-stepping for no saving.
TILE_EXTENT = 4096
TILE_BUFFER = 256


def css_pixel_size_metres(zoom: int) -> float:
    """Width of one CSS pixel at this zoom, in Web Mercator metres."""
    return EARTH_CIRCUMFERENCE_M / (TILE_CSS_PIXELS * 2**zoom)


def snap_size_metres(zoom: int) -> float:
    """Grid the geometry is snapped to before rendering this zoom."""
    css_pixels = (
        COARSE_SNAP_CSS_PIXELS if zoom < COARSE_SNAP_BELOW_ZOOM else SNAP_CSS_PIXELS
    )
    return css_pixel_size_metres(zoom) * css_pixels


class WaterwayVectorLayer(VectorLayer):
    """One waterway type, rendered differently above and below the cache line.

    Rendered with explicit SQL rather than through the library's queryset
    machinery, which cannot express either the snap-to-grid or the merge.
    """

    #: Value of the `waterway` tag this layer draws.
    waterway = None
    #: Merge into a single geometry at cached zooms. Drops per-feature ids, so
    #: only for layers that do not need to be clickable when zoomed out.
    merge_when_cached = False

    geom_field = "geometry"
    min_zoom = 0

    def _tile_envelope(self) -> str:
        return "ST_TileEnvelope(%(z)s, %(x)s, %(y)s)"

    def _cached_zoom_sql(self) -> str:
        """Render from the Web Mercator tile table, generalised for this zoom."""
        table = f'"{settings.OSM_SCHEMA}"."waterway_tile"'
        envelope = self._tile_envelope()
        # Already 3857, so it can be snapped directly.
        snapped = "ST_RemoveRepeatedPoints(ST_SnapToGrid(geometry, %(px)s), %(px)s)"

        if self.merge_when_cached:
            projection = (
                f"ST_AsMVTGeom(ST_LineMerge(ST_Collect({snapped})), {envelope}, "
                f"{TILE_EXTENT}, {TILE_BUFFER}, true) AS geom"
            )
        else:
            projection = (
                f"osm_way_id, ST_AsMVTGeom({snapped}, {envelope}, "
                f"{TILE_EXTENT}, {TILE_BUFFER}, true) AS geom"
            )

        return f"""
            SELECT ST_AsMVT(q, %(layer)s, {TILE_EXTENT}, 'geom') FROM (
                SELECT {projection}
                FROM {table}
                WHERE waterway = %(waterway)s AND geometry && {envelope}
            ) q WHERE q.geom IS NOT NULL
        """  # noqa: S608

    def _full_detail_sql(self) -> str:
        """Render per-feature from the canonical 4326 table.

        Still snapped to the pixel grid: a vertex finer than one pixel cannot
        be drawn at *any* zoom, and MapLibre requests a fresh tile for each
        zoom level, so there is no overzoom to reveal the quantisation. Left
        unsnapped, a dense zoom 10 tile is an order of magnitude bigger for no
        visible gain.
        """
        table = f'"{settings.OSM_SCHEMA}"."waterway_section"'
        envelope = self._tile_envelope()
        snapped = (
            "ST_RemoveRepeatedPoints("
            "ST_SnapToGrid(ST_Transform(geometry, 3857), %(px)s), %(px)s)"
        )
        return f"""
            SELECT ST_AsMVT(q, %(layer)s, {TILE_EXTENT}, 'geom') FROM (
                SELECT osm_way_id,
                       ST_AsMVTGeom({snapped}, {envelope},
                                    {TILE_EXTENT}, {TILE_BUFFER}, true) AS geom
                FROM {table}
                -- Compared in 4326 so the GiST index on the canonical
                -- geometry column can be used.
                WHERE waterway = %(waterway)s
                  AND geometry && ST_Transform({envelope}, 4326)
            ) q WHERE q.geom IS NOT NULL
        """  # noqa: S608

    def _render(self, x: int, y: int, z: int) -> bytes:
        cached_zoom = z < CACHE_BELOW_ZOOM_LEVEL
        sql = self._cached_zoom_sql() if cached_zoom else self._full_detail_sql()

        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                {
                    "z": z,
                    "x": x,
                    "y": y,
                    "px": snap_size_metres(z),
                    "layer": self.get_id(),
                    "waterway": self.waterway,
                },
            )
            row = cursor.fetchone()

        tile = row[0] if row else None
        if tile is None:
            return b""
        # psycopg2 hands back a memoryview, psycopg3 bytes.
        return tile.tobytes() if isinstance(tile, memoryview) else bytes(tile)

    def get_tile(self, x, y, z):
        """Serve a tile, caching the low zoom levels.

        Cached entries are evicted individually by the sync task using
        osm2pgsql's expire output, so the cache stays fresh without a blanket
        flush. The data version in the key covers full rebuilds, where
        osm2pgsql emits no expiry information at all.
        """
        if not self.check_in_zoom_levels(z):
            return b""

        if z >= CACHE_BELOW_ZOOM_LEVEL:
            return self._render(x, y, z)

        cache_key = tile_cache_key(self.get_id(), z, x, y, get_data_version())
        tile = cache.get(cache_key)
        if tile is not None:
            return tile

        tile = self._render(x, y, z)
        cache.set(cache_key, tile, timeout=TILE_CACHE_TIMEOUT_SECS)
        return tile


class RiverSectionVectorLayer(WaterwayVectorLayer):
    """Named rivers: drawn heavier, and clickable at every zoom.

    Keeps its historical layer id, because the front end styles and binds
    click handlers to it by name.
    """

    id = "river-sections"
    description = "OSM ways forming a river"
    waterway = "river"
    layer_fields = {"osm_way_id": "OSM way id, for the popup lookup"}
    merge_when_cached = False


class StreamSectionVectorLayer(WaterwayVectorLayer):
    """Streams and creeks: the textured wash behind the rivers.

    Merged into a single geometry at cached zooms, so it carries no
    osm_way_id there and cannot be clicked when zoomed out — which is no loss,
    since the lines are barely a pixel wide. Above CACHE_BELOW_ZOOM_LEVEL the
    merging stops and individual streams become clickable again.
    """

    id = "stream-sections"
    description = "OSM ways forming a stream"
    waterway = "stream"
    layer_fields = {"osm_way_id": "OSM way id; absent at cached zooms, when merged"}
    merge_when_cached = True
