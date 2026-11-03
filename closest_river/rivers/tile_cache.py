"""Tile caching and targeted invalidation.

Tiles below CACHE_BELOW_ZOOM_LEVEL are cached, one entry per z/x/y holding
every layer, already gzipped. That spares gzipping the same tile again on
every request, which for the biggest low-zoom tiles is tens of milliseconds,
and it roughly halves the cache: measured, the snapped geometry compresses
about five-fold at z0-z2 but only 1.3-fold at z10, where most entries are.
Per layer would not do, because the view joins the layers' bytes and two gzip
streams back to back are not one.

Rather than dropping the whole cache whenever data changes, osm2pgsql tells us
exactly which tiles a replication diff invalidated (see the expire output in
waterways.lua) and we delete only those keys.

Never call ``cache.clear()`` here: django-redis implements it as FLUSHDB, and a
misconfigured deployment could point the cache at the Celery broker's database.
"""

import gzip
import logging
import time
from dataclasses import dataclass
from hashlib import md5

from django.core.cache import cache
from django.db import ProgrammingError
from django.db import connection

logger = logging.getLogger(__name__)

# Tiles from zoom 0 to CACHE_BELOW_ZOOM_LEVEL - 1 are eligible for caching.
# Must stay in step with the expire output's maxzoom in waterways.lua.
# Caching reaches past the low zooms into z9-z10, where the tiles are smaller
# but the densest ones still cost around half a second to render. Must stay in
# step with MAX_CACHED_ZOOM in waterways.lua, which decides how far up tile
# expiry is emitted.
CACHE_BELOW_ZOOM_LEVEL = 11
# A long TTL is safe now that the sync evicts changed tiles individually, and
# worth having: a low-zoom tile costs up to a second to merge, so it should be
# generated rarely. This is only a backstop against invalidation ever missing
# something; a full rebuild strands everything via the data version instead.
TILE_CACHE_TIMEOUT_SECS = 7 * 24 * 60 * 60

EXPIRE_TABLE_NAME = "tile_expire"

_DATA_VERSION_CACHE_KEY = "rivers:data-version"
# Short TTL: read on every cached tile request, so it must not hit the database
# each time, but it also must not go stale for long after a rebuild.
_DATA_VERSION_CACHE_SECS = 10

# How often precaching logs how far it has got. A batch runs for minutes and a
# sync's precache for up to hours, one tile at a time.
PRECACHE_PROGRESS_SECONDS = 30


def get_data_version() -> int:
    """Current data generation, bumped only by a full rebuild."""
    version = cache.get(_DATA_VERSION_CACHE_KEY)
    if version is not None:
        return version

    from closest_river.rivers.models import SyncState

    version = SyncState.load().data_version
    cache.set(_DATA_VERSION_CACHE_KEY, version, timeout=_DATA_VERSION_CACHE_SECS)
    return version


def bump_data_version() -> int:
    """Strand every cached tile at once. Only for full rebuilds."""
    from django.db.models import F

    from closest_river.rivers.models import SyncState

    # load() first: a bare filter().update() would silently do nothing on the
    # very first import, when the singleton row does not exist yet.
    SyncState.load()
    SyncState.objects.filter(pk=SyncState.SINGLETON_PK).update(
        data_version=F("data_version") + 1,
    )
    cache.delete(_DATA_VERSION_CACHE_KEY)
    version = SyncState.load().data_version
    logger.info("Tile data version bumped to %s", version)
    return version


def tile_cache_key(z: int, x: int, y: int, version: int) -> str:
    # "gzip" keeps these apart from the per-layer, uncompressed entries that
    # came before, which would otherwise be served with a gzip header.
    return md5(  # noqa: S324
        f"gzip-{version}-{z}-{x}-{y}".encode(),
    ).hexdigest()


def cached_tile(layers, z: int, x: int, y: int) -> bytes:
    """Every layer's tile at z/x/y, gzipped, from the cache or rendered into it.

    An empty tile is cached as b"" rather than gzipped, so the view can still
    answer 204 without decompressing anything.
    """
    key = tile_cache_key(z, x, y, get_data_version())
    tile = cache.get(key)
    if tile is not None:
        return tile

    # Concatenating encoded MVTs merges them: the layers field is repeated.
    tile = b"".join(layer.get_tile(x, y, z) for layer in layers)
    if tile:
        # mtime=0 so the same tile always compresses to the same bytes.
        tile = gzip.compress(tile, mtime=0)
    cache.set(key, tile, timeout=TILE_CACHE_TIMEOUT_SECS)
    return tile


@dataclass(frozen=True)
class ZoomCoverage:
    zoom: int
    cached: int
    # Bytes the cached tiles take in Redis, keys and overhead included, or None
    # where the cache backend cannot say.
    memory: int | None

    @property
    def total(self) -> int:
        return 4**self.zoom

    @property
    def percent(self) -> float:
        return 100 * self.cached / self.total


def cache_coverage(max_zoom: int) -> list[ZoomCoverage]:
    """How many of each zoom's tiles are cached, from zoom 0 to max_zoom.

    Checks every tile's key, so the cost grows four-fold a zoom: zoom 0-8 is
    87,381 keys, a quarter of a second against a local Valkey. Zoom 9-10
    would be 1.3 million.
    """
    version = get_data_version()
    redis = _redis_connection()
    coverage = []
    for z in range(max_zoom + 1):
        keys = [
            tile_cache_key(z, x, y, version)
            for x in range(1 << z)
            for y in range(1 << z)
        ]
        if redis is None:
            cached, memory = sum(cache.has_key(key) for key in keys), None
        else:
            pipeline = redis.pipeline(transaction=False)
            for key in keys:
                pipeline.memory_usage(cache.make_key(key))
            sizes = [size for size in pipeline.execute() if size is not None]
            cached, memory = len(sizes), sum(sizes)
        coverage.append(ZoomCoverage(zoom=z, cached=cached, memory=memory))
    return coverage


def cache_stats() -> dict | None:
    """Redis's own figures: memory used, maxmemory, the eviction policy, and
    how many keys it has evicted since it started, which is how long ago
    uptime says. Evictions are the sign the cache is too small: a few after a
    burst are expected, a steady climb is not.
    """
    redis = _redis_connection()
    if redis is None:
        return None
    info = redis.info()
    return {
        "used": info["used_memory"],
        "max": info["maxmemory"],
        "policy": info["maxmemory_policy"],
        "evicted": info["evicted_keys"],
        "uptime": info["uptime_in_seconds"],
    }


def _redis_connection():
    """The cache's Redis client, or None if the cache is not django-redis, as
    in the tests, which use the local-memory cache.
    """
    from django_redis import get_redis_connection

    try:
        return get_redis_connection("default")
    except NotImplementedError:
        return None


def expire_table() -> str:
    """Schema-qualified expire table. The schema is an --schema argument, so it
    is configuration rather than a constant.
    """
    from django.conf import settings

    return f'"{settings.OSM_SCHEMA}"."{EXPIRE_TABLE_NAME}"'


def _drain_expired_tiles() -> list[tuple[int, int, int]]:
    """Atomically take and clear the tiles osm2pgsql marked as expired.

    A single DELETE ... RETURNING avoids the race where an append adds rows
    between a SELECT and a TRUNCATE. osm2pgsql does not clear this table
    itself.
    """
    table = expire_table()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table} RETURNING zoom, x, y")  # noqa: S608
            return cursor.fetchall()
    except ProgrammingError:
        # No import has run yet, so osm2pgsql has not created the table.
        logger.warning("%s does not exist; nothing to invalidate", table)
        return []


def invalidate_expired_tiles() -> set[tuple[int, int, int]]:
    """Evict the cached tiles affected by the last replication update.

    Returns the (z, x, y) tiles evicted.
    """
    expired = _drain_expired_tiles()
    if not expired:
        return set()

    # osm2pgsql refuses minzoom=0, so zoom 0 never appears in the expire table.
    # It is a single tile that any change at all invalidates, so drop it
    # whenever anything else expired.
    tiles = set(expired) | {(0, 0, 0)}

    version = get_data_version()
    cache.delete_many([tile_cache_key(z, x, y, version) for (z, x, y) in tiles])

    logger.info(
        "Invalidated %s cached tiles from %s expired entries",
        len(tiles),
        len(expired),
    )
    return tiles


def high_effort_tiles(
    tiles: set[tuple[int, int, int]],
    max_zoom: int,
) -> list[tuple[int, int, int]]:
    """The tiles worth rendering ahead of the first request for them.

    The lower the zoom, the more of the network a tile merges and the more
    visitors share it, so these come first: a precache cut short then loses
    only the cheapest tiles.
    """
    return sorted(tile for tile in tiles if tile[0] <= max_zoom)


def precache_tiles(layers, tiles: list[tuple[int, int, int]]) -> int:
    """Render tiles into the cache so no visitor waits on them.

    Goes through cached_tile, as the view does, so a tile someone has requested
    since it was evicted is a cache hit rather than a second render.

    Logs how far it has got every PRECACHE_PROGRESS_SECONDS.

    Returns the number of tiles rendered or found cached.
    """
    # Imported here: osm_pipeline imports this module.
    from closest_river.rivers.osm_pipeline import format_duration

    started = last_report = time.monotonic()
    for done, (z, x, y) in enumerate(tiles, start=1):
        cached_tile(layers, z, x, y)
        now = time.monotonic()
        if now - last_report >= PRECACHE_PROGRESS_SECONDS and done < len(tiles):
            last_report = now
            logger.info(
                "Precached %s of %s tiles (%d%%), at zoom %s, in %s",
                done,
                len(tiles),
                100 * done // len(tiles),
                z,
                format_duration(now - started),
            )
    return len(tiles)
