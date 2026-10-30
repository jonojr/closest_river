"""Targeted tile invalidation.

The behaviour that matters: a sync must evict the tiles a diff actually
changed, leave everything else alone, and never flush the whole cache.
"""

import pytest
from django.core.cache import cache
from django.db import connection

from closest_river.rivers import tile_cache
from closest_river.rivers.models import SyncState

pytestmark = pytest.mark.django_db

LAYER = "river-sections"


def _expire(*tiles):
    with connection.cursor() as cursor:
        for zoom, x, y in tiles:
            cursor.execute(
                "INSERT INTO osm.tile_expire (zoom, x, y) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [zoom, x, y],
            )


def _cache_tile(zoom, x, y, value=b"tile"):
    key = tile_cache.tile_cache_key(LAYER, zoom, x, y, tile_cache.get_data_version())
    cache.set(key, value, timeout=600)
    return key


def test_evicts_expired_tiles_and_keeps_the_rest():
    changed = _cache_tile(6, 33, 22)
    untouched = _cache_tile(6, 99, 99)
    _expire((6, 33, 22))

    tile_cache.invalidate_expired_tiles([LAYER])

    assert cache.get(changed) is None
    assert cache.get(untouched) == b"tile", "unrelated tiles must survive a sync"


def test_always_evicts_zoom_zero():
    """osm2pgsql rejects minzoom=0, so z0 is handled by us.

    Zoom 0 is a single tile covering the planet, so any change anywhere
    invalidates it, but it will never appear in the expire table.
    """
    zero = _cache_tile(0, 0, 0)
    _expire((4, 8, 5))

    tile_cache.invalidate_expired_tiles([LAYER])

    assert cache.get(zero) is None


def test_drains_the_expire_table():
    _expire((3, 4, 2), (5, 16, 11))

    tile_cache.invalidate_expired_tiles([LAYER])

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM osm.tile_expire")
        assert cursor.fetchone()[0] == 0, "osm2pgsql does not clear this table for us"


def test_no_expired_tiles_is_a_no_op():
    cached = _cache_tile(6, 1, 1)

    assert tile_cache.invalidate_expired_tiles([LAYER]) == 0
    assert cache.get(cached) == b"tile"


def test_bumping_the_data_version_strands_every_tile():
    """A full rebuild emits no expiry information, so the key changes instead."""
    before = tile_cache.tile_cache_key(LAYER, 3, 4, 2, tile_cache.get_data_version())

    tile_cache.bump_data_version()

    after = tile_cache.tile_cache_key(LAYER, 3, 4, 2, tile_cache.get_data_version())
    assert before != after
    assert SyncState.load().data_version == 2


def test_missing_expire_table_is_survivable():
    """Before the first import osm2pgsql has not created the table yet."""
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE osm.tile_expire")

    assert tile_cache.invalidate_expired_tiles([LAYER]) == 0
