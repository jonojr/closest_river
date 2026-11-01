"""Precaching the costly tiles a daily sync evicts.

Low-zoom tiles merge most of the network and take up to a second each, so the
sync renders the ones it evicted back into the cache rather than leaving the
first visitor to wait on every one.
"""

import mercantile
import pytest
from django.contrib.gis.geos import LineString
from django.core.cache import cache
from django.db import connection

from closest_river.rivers import osm_pipeline
from closest_river.rivers import tasks
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tile_cache import get_data_version
from closest_river.rivers.tile_cache import tile_cache_key

pytestmark = pytest.mark.django_db

# Melbourne, in Web Mercator.
SEED_X, SEED_Y = 16132000, -4554000
SEED_LON, SEED_LAT = 144.92, -37.82


@pytest.fixture
def queued(monkeypatch):
    """Stub out osm2pgsql and capture the precache task instead of queueing it."""
    for step in ("replication_update", "apply_river_intermittency", "analyze_schema"):
        monkeypatch.setattr(osm_pipeline, step, lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(tasks.precache_high_effort_tiles, "delay", calls.append)
    return calls


def _expire(*tiles):
    with connection.cursor() as cursor:
        for zoom, x, y in tiles:
            cursor.execute(
                "INSERT INTO osm.tile_expire (zoom, x, y) VALUES (%s, %s, %s)",
                [zoom, x, y],
            )


def test_the_sync_queues_the_low_zoom_tiles_it_evicted(queued, settings):
    settings.TILE_PRECACHE_MAX_ZOOM = 8
    _expire((10, 900, 600), (8, 230, 155), (4, 14, 9))

    tasks.sync_osm_waterways()

    assert queued == [[(0, 0, 0), (4, 14, 9), (8, 230, 155)]]


def test_nothing_is_queued_when_nothing_changed(queued):
    tasks.sync_osm_waterways()

    assert queued == []


def test_a_negative_max_zoom_turns_precaching_off(queued, settings):
    settings.TILE_PRECACHE_MAX_ZOOM = -1
    _expire((4, 14, 9))

    tasks.sync_osm_waterways()

    assert queued == []


def test_precaching_puts_the_tiles_in_the_cache():
    WaterwayTile.objects.create(
        osm_way_id=1,
        waterway="river",
        geometry=LineString(
            (SEED_X, SEED_Y),
            (SEED_X + 600_000, SEED_Y - 600_000),
            srid=3857,
        ),
    )
    tile = mercantile.tile(SEED_LON, SEED_LAT, 6)
    key = tile_cache_key(6, tile.x, tile.y, get_data_version())

    # As it arrives from the broker: JSON has turned each tuple into a list.
    tasks.precache_high_effort_tiles([[6, tile.x, tile.y]])

    assert cache.get(key), "the river tile should be cached before anyone asks"
