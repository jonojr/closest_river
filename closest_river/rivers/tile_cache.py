"""Tile cache keys and targeted invalidation.

Tiles below CACHE_BELOW_ZOOM_LEVEL are cached. Rather than dropping the whole
cache whenever data changes, osm2pgsql tells us exactly which tiles a
replication diff invalidated (see the expire output in waterways.lua) and we
delete only those keys.

Never call ``cache.clear()`` here: django-redis implements it as FLUSHDB, and a
misconfigured deployment could point the cache at the Celery broker's database.
"""

import logging
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


def tile_cache_key(layer_id: str, z: int, x: int, y: int, version: int) -> str:
    return md5(  # noqa: S324
        f"{layer_id}-{version}-{z}-{x}-{y}".encode(),
    ).hexdigest()


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


def invalidate_expired_tiles(layer_ids: list[str]) -> int:
    """Evict the cached tiles affected by the last replication update.

    Returns the number of cache keys deleted.
    """
    expired = _drain_expired_tiles()
    if not expired:
        return 0

    # osm2pgsql refuses minzoom=0, so zoom 0 never appears in the expire table.
    # It is a single tile that any change at all invalidates, so drop it
    # whenever anything else expired.
    tiles = set(expired) | {(0, 0, 0)}

    version = get_data_version()
    keys = [
        tile_cache_key(layer_id, z, x, y, version)
        for layer_id in layer_ids
        for (z, x, y) in tiles
    ]
    cache.delete_many(keys)

    logger.info(
        "Invalidated %s cached tiles across %s layer(s) from %s expired entries",
        len(keys),
        len(layer_ids),
        len(expired),
    )
    return len(keys)
