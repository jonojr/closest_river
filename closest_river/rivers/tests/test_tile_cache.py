"""Targeted tile invalidation.

The behaviour that matters: a sync must evict the tiles a diff actually
changed, leave everything else alone, and never flush the whole cache.
"""

import gzip
import logging

import pytest
from django.core.cache import cache
from django.db import connection

from closest_river.rivers import tile_cache
from closest_river.rivers.models import SyncState

pytestmark = pytest.mark.django_db


def _expire(*tiles):
    with connection.cursor() as cursor:
        for zoom, x, y in tiles:
            cursor.execute(
                "INSERT INTO osm.tile_expire (zoom, x, y) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [zoom, x, y],
            )


def _cache_tile(zoom, x, y, value=b"tile"):
    key = tile_cache.tile_cache_key(zoom, x, y, tile_cache.get_data_version())
    cache.set(key, value, timeout=600)
    return key


def test_evicts_expired_tiles_and_keeps_the_rest():
    changed = _cache_tile(6, 33, 22)
    untouched = _cache_tile(6, 99, 99)
    _expire((6, 33, 22))

    tile_cache.invalidate_expired_tiles()

    assert cache.get(changed) is None
    assert cache.get(untouched) == b"tile", "unrelated tiles must survive a sync"


def test_always_evicts_zoom_zero():
    """osm2pgsql rejects minzoom=0, so z0 is handled by us.

    Zoom 0 is a single tile covering the planet, so any change anywhere
    invalidates it, but it will never appear in the expire table.
    """
    zero = _cache_tile(0, 0, 0)
    _expire((4, 8, 5))

    tile_cache.invalidate_expired_tiles()

    assert cache.get(zero) is None


def test_drains_the_expire_table():
    _expire((3, 4, 2), (5, 16, 11))

    tile_cache.invalidate_expired_tiles()

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM osm.tile_expire")
        assert cursor.fetchone()[0] == 0, "osm2pgsql does not clear this table for us"


def test_no_expired_tiles_is_a_no_op():
    cached = _cache_tile(6, 1, 1)

    assert tile_cache.invalidate_expired_tiles() == set()
    assert cache.get(cached) == b"tile"


def test_bumping_the_data_version_strands_every_tile():
    """A full rebuild emits no expiry information, so the key changes instead."""
    before = tile_cache.tile_cache_key(3, 4, 2, tile_cache.get_data_version())

    tile_cache.bump_data_version()

    after = tile_cache.tile_cache_key(3, 4, 2, tile_cache.get_data_version())
    assert before != after
    assert SyncState.load().data_version == 2


def test_missing_expire_table_is_survivable():
    """Before the first import osm2pgsql has not created the table yet."""
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE osm.tile_expire")

    assert tile_cache.invalidate_expired_tiles() == set()


def test_returns_the_tiles_it_evicted_including_zoom_zero():
    _expire((4, 8, 5), (6, 33, 22))

    evicted = tile_cache.invalidate_expired_tiles()

    assert evicted == {(0, 0, 0), (4, 8, 5), (6, 33, 22)}


def test_high_effort_tiles_are_the_low_zooms_lowest_first():
    """A precache cut short should lose only the cheapest tiles."""
    tiles = {(10, 900, 600), (0, 0, 0), (6, 33, 22), (3, 4, 2), (8, 230, 155)}

    assert tile_cache.high_effort_tiles(tiles, max_zoom=8) == [
        (0, 0, 0),
        (3, 4, 2),
        (6, 33, 22),
        (8, 230, 155),
    ]


def test_a_negative_max_zoom_precaches_nothing():
    assert tile_cache.high_effort_tiles({(0, 0, 0), (4, 8, 5)}, max_zoom=-1) == []


class FakeLayer:
    def __init__(self, content):
        self.content = content
        self.rendered = []

    def get_tile(self, x, y, z):
        self.rendered.append((z, x, y))
        return self.content


def test_a_tile_is_cached_once_for_every_layer_and_gzipped():
    """One entry per tile: two gzip streams joined back to back are not one."""
    layers = [FakeLayer(b"streams"), FakeLayer(b"rivers")]

    tile = tile_cache.cached_tile(layers, 6, 33, 22)

    assert gzip.decompress(tile) == b"streamsrivers"
    key = tile_cache.tile_cache_key(6, 33, 22, tile_cache.get_data_version())
    assert cache.get(key) == tile


def test_a_cached_tile_is_not_rendered_again():
    layers = [FakeLayer(b"streams")]

    tile_cache.cached_tile(layers, 6, 33, 22)
    tile_cache.cached_tile(layers, 6, 33, 22)

    assert layers[0].rendered == [(6, 33, 22)]


def test_an_empty_tile_is_cached_uncompressed():
    """So the view can answer 204 without decompressing anything."""
    assert tile_cache.cached_tile([FakeLayer(b"")], 6, 33, 22) == b""


def test_precaching_renders_each_tile_in_every_layer():
    layers = [FakeLayer(b"streams"), FakeLayer(b"rivers")]

    assert tile_cache.precache_tiles(layers, [(0, 0, 0), (4, 8, 5)]) == 2
    assert all(layer.rendered == [(0, 0, 0), (4, 8, 5)] for layer in layers)


def test_precaching_logs_its_progress(monkeypatch, caplog):
    monkeypatch.setattr(tile_cache, "PRECACHE_PROGRESS_SECONDS", 0)
    caplog.set_level(logging.INFO, logger=tile_cache.__name__)

    tile_cache.precache_tiles([FakeLayer(b"rivers")], [(0, 0, 0), (1, 0, 0), (1, 1, 0)])

    progress = [r.getMessage() for r in caplog.records]
    assert progress[0].startswith("Precached 1 of 3 tiles (33%), at zoom 0, in ")
    assert progress[1].startswith("Precached 2 of 3 tiles (66%), at zoom 1, in ")
    assert len(progress) == 2, "the task itself reports finishing"


def test_precaching_logs_nothing_before_the_first_interval(caplog):
    caplog.set_level(logging.INFO, logger=tile_cache.__name__)

    tile_cache.precache_tiles([FakeLayer(b"rivers")], [(0, 0, 0), (1, 0, 0)])

    assert caplog.records == []
