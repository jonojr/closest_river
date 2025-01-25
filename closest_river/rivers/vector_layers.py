from hashlib import md5

from django.core.cache import cache
from vectortiles import VectorLayer

from closest_river.rivers.models import RiverSection

CACHE_BELOW_ZOOM_LEVEL = 9  # All tiles from zoom levels 0-8 are eligible for caching.
TILE_CACHE_TIMEOUT_SECS = 60 * 60  # 1 hr tile cache.


class RiverSectionVectorLayer(VectorLayer):
    model = RiverSection
    geom_field = "geometry"
    id = "river-sections"
    tile_fields = ("name", "tags", "river__tags", "river__osm_id")
    min_zoom = 0

    def get_tile(self, x, y, z):
        """Get tile at x,y,z, caching tiles for some zoom levels"""
        if z < CACHE_BELOW_ZOOM_LEVEL:
            cache_key = md5(f"{self.get_id()}-{z}-{x}-{y}".encode()).hexdigest()  # noqa: S324
            if cache.has_key(cache_key):
                return cache.get(cache_key)
            tile = super().get_tile(x, y, z)
            cache.set(cache_key, tile, timeout=TILE_CACHE_TIMEOUT_SECS)
            return tile

        return super().get_tile(x, y, z)
