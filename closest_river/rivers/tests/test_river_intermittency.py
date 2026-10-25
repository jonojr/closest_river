"""Relation members are drawn the way their river is.

Tagged way by way, a mostly permanent river broke up into dashes wherever a
stretch happened to be intermittent. apply_river_intermittency() gives every
member its river's verdict instead.
"""

import mercantile
import pytest
from django.contrib.gis.geos import LineString
from django.db import connection

from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tests.factories import RiverFactory
from closest_river.rivers.tests.factories import RiverSectionFactory
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL

pytestmark = pytest.mark.django_db

INTERMITTENT = {"waterway": "river", "intermittent": "yes"}
PERENNIAL = {"waterway": "river"}


def _section(tags, river=None):
    """A section and its tile row, flagged from its own tags as the Lua does."""
    section = RiverSectionFactory(river=river, tags=tags)
    WaterwayTile.objects.create(
        osm_way_id=section.osm_way_id,
        waterway=section.waterway,
        intermittent=section.intermittent,
        geometry=section.geometry.transform(3857, clone=True),
    )
    return section


def _drawn_intermittent(section) -> bool:
    return WaterwayTile.objects.get(osm_way_id=section.osm_way_id).intermittent


def _expired() -> set[tuple[int, int, int]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT zoom, x, y FROM osm.tile_expire")
        return set(cursor.fetchall())


def test_a_mostly_permanent_river_is_drawn_permanent_throughout():
    river = RiverFactory()
    dry = _section(INTERMITTENT, river)
    wet = [_section(PERENNIAL, river) for _ in range(2)]

    restyled = osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert restyled == 1
    assert not _drawn_intermittent(dry)
    assert not any(_drawn_intermittent(section) for section in wet)


def test_a_mostly_intermittent_river_is_drawn_intermittent_throughout():
    """Cooper Creek: 299 of 308 sections, but the relation is untagged."""
    river = RiverFactory()
    for _ in range(2):
        _section(INTERMITTENT, river)
    wet = _section(PERENNIAL, river)

    osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert _drawn_intermittent(wet)


def test_a_tagged_relation_is_drawn_intermittent_whatever_its_sections_say():
    river = RiverFactory(tags={"type": "waterway", "intermittent": "yes"})
    wet = _section(PERENNIAL, river)

    osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert _drawn_intermittent(wet)


def test_a_way_in_no_relation_keeps_its_own_value():
    alone = _section(INTERMITTENT)

    osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert _drawn_intermittent(alone)


def test_a_way_whose_relation_is_missing_keeps_its_own_value():
    """As on the result card, which can only fall back to the section."""
    orphan = RiverSectionFactory(river_id=123456, tags=INTERMITTENT)
    WaterwayTile.objects.create(
        osm_way_id=orphan.osm_way_id,
        waterway="river",
        intermittent=True,
        geometry=orphan.geometry.transform(3857, clone=True),
    )

    osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert _drawn_intermittent(orphan)


def test_agrees_with_the_river_model():
    river = RiverFactory()
    for tags in (INTERMITTENT, INTERMITTENT, PERENNIAL, PERENNIAL):
        _section(tags, river)

    osm_pipeline.apply_river_intermittency("osm", expire=False)

    drawn = {_drawn_intermittent(section) for section in river.sections.all()}
    assert drawn == {river.is_mostly_intermittent()}


def test_reapplying_changes_nothing():
    river = RiverFactory()
    _section(INTERMITTENT, river)
    _section(PERENNIAL, river)
    osm_pipeline.apply_river_intermittency("osm", expire=False)

    assert osm_pipeline.apply_river_intermittency("osm", expire=True) == 0
    assert not _expired(), "a sync that restyles nothing must evict nothing"


def test_expires_the_tiles_of_every_way_it_restyles():
    """A one-way retag can restyle a whole river, far beyond anything
    osm2pgsql expired, so the sync has to be told about the rest."""
    river = RiverFactory()
    dry = _section(INTERMITTENT, river)
    for _ in range(2):
        _section(PERENNIAL, river)

    osm_pipeline.apply_river_intermittency("osm", expire=True)

    expired = _expired()
    lon, lat = dry.geometry.coords[0]
    for zoom in range(1, CACHE_BELOW_ZOOM_LEVEL):
        tile = mercantile.tile(lon, lat, zoom)
        assert (zoom, tile.x, tile.y) in expired
    # osm2pgsql cannot emit z0, so tile_cache always evicts it; and nothing
    # above the cached zooms is worth listing.
    assert {zoom for zoom, _, _ in expired} == set(range(1, CACHE_BELOW_ZOOM_LEVEL))


def test_expiry_includes_the_tile_buffer_and_no_more():
    """A line just inside one tile is still drawn in its neighbour's buffer."""
    boundary = mercantile.bounds(922, 627, 10).west
    river = RiverFactory()
    dry = RiverSectionFactory(
        river=river,
        tags=INTERMITTENT,
        geometry=LineString(
            (boundary + 0.001, -37.60),
            (boundary + 0.002, -37.61),
            srid=4326,
        ),
    )
    WaterwayTile.objects.create(
        osm_way_id=dry.osm_way_id,
        waterway="river",
        intermittent=True,
        geometry=dry.geometry.transform(3857, clone=True),
    )
    for _ in range(2):
        _section(PERENNIAL, river)

    osm_pipeline.apply_river_intermittency("osm", expire=True)

    z10 = {(x, y) for zoom, x, y in _expired() if zoom == 10}
    assert {(921, 627), (922, 627)} <= z10
    assert (923, 627) not in z10
