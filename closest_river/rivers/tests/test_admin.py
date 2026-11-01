"""The pipeline's tables are inspectable in the admin, and only inspectable."""

from http import HTTPStatus

import pytest
from django.contrib.gis.geos import LineString
from django.db import connection
from django.urls import reverse

from closest_river.rivers.admin import _tiles_crossed
from closest_river.rivers.models import SyncState
from closest_river.rivers.models import WaterwayTile

pytestmark = pytest.mark.django_db


@pytest.fixture
def sync_state():
    state = SyncState.load()
    state.last_sync_tiles_expired = 12762
    state.save()
    return state


@pytest.fixture
def waterway_tile():
    return WaterwayTile.objects.create(
        osm_way_id=4242,
        waterway="stream",
        intermittent=True,
        geometry=LineString((0, 0), (1000, 1000), srid=3857),
    )


def test_sync_state_shows_the_last_sync(admin_client, sync_state):
    response = admin_client.get(reverse("admin:rivers_syncstate_changelist"))

    assert response.status_code == HTTPStatus.OK
    assert "Cache keys evicted by the last sync" in response.text
    assert "12762" in response.text


def test_sync_state_detail_page_loads(admin_client, sync_state):
    url = reverse("admin:rivers_syncstate_change", args=[sync_state.pk])

    response = admin_client.get(url)

    assert response.status_code == HTTPStatus.OK
    assert "12762" in response.text


def test_sync_state_cannot_be_edited(admin_client, sync_state):
    url = reverse("admin:rivers_syncstate_change", args=[sync_state.pk])

    admin_client.post(url, {"data_version": 99})

    assert SyncState.load().data_version == sync_state.data_version
    response = admin_client.get(reverse("admin:rivers_syncstate_add"))
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_waterway_tiles_can_be_found_by_way_id(admin_client, waterway_tile):
    url = reverse("admin:rivers_waterwaytile_changelist")

    response = admin_client.get(url, {"q": "4242", "intermittent__exact": "1"})

    assert response.status_code == HTTPStatus.OK
    assert "4242" in response.text


def test_waterway_tile_detail_page_loads(admin_client, waterway_tile):
    url = reverse("admin:rivers_waterwaytile_change", args=[waterway_tile.pk])

    assert admin_client.get(url).status_code == HTTPStatus.OK


def _waterway_tile(osm_way_id, *coords):
    return WaterwayTile.objects.create(
        osm_way_id=osm_way_id,
        waterway="river",
        geometry=LineString(*coords, srid=3857),
    )


def test_tiles_are_the_ones_the_line_reaches_not_its_bounding_box():
    """x + y = 4,000 km: through the NW, NE and SE quarters of the world.

    Its bounding box covers the SW quarter too, but the line never comes
    within that tile's buffer (1/16 of a tile, 1,250 km at z1), so an edit to
    it cannot have changed that tile.
    """
    _waterway_tile(1, (-2_000_000, 6_000_000), (6_000_000, -2_000_000))

    at_z1 = [(x, y) for z, x, y in _tiles_crossed(1) if z == 1]

    assert at_z1 == [(0, 0), (1, 0), (1, 1)]


def test_a_line_just_outside_a_tile_is_drawn_in_its_buffer():
    """10 km west of the z1 meridian: inside the eastern tiles' buffer."""
    _waterway_tile(1, (-10_000, 5_000_000), (-10_000, 6_000_000))

    at_z1 = [(x, y) for z, x, y in _tiles_crossed(1) if z == 1]

    assert at_z1 == [(0, 0), (1, 0)]


def test_every_cached_zoom_is_listed():
    _waterway_tile(1, (16132000, -4554000), (16132100, -4554100))

    assert {z for z, _, _ in _tiles_crossed(1)} == set(range(11))


def test_the_detail_page_lists_the_tiles(admin_client):
    _waterway_tile(1, (-2_000_000, 6_000_000), (6_000_000, -2_000_000))
    url = reverse("admin:rivers_waterwaytile_change", args=[1])

    response = admin_client.get(url)

    assert "Cached tiles it is drawn in" in response.text
    assert "z1: (0, 0), (1, 0), (1, 1)" in response.text


def test_a_long_way_summarises_its_tiles(admin_client):
    # 1,200 km east-west, and a z10 tile is 39 km across: about 31 tiles.
    _waterway_tile(1, (15132000, -4554000), (16332000, -4554000))
    url = reverse("admin:rivers_waterwaytile_change", args=[1])

    response = admin_client.get(url)

    assert "and 11 more" in response.text or "and 12 more" in response.text


def test_sync_state_shows_how_current_the_data_is(
    admin_client,
    sync_state,
    replication_properties,
):
    replication_properties(
        replication_timestamp="2026-07-19T20:20:51Z",
        replication_sequence_number="4949",
        replication_base_url="https://planet.openstreetmap.org/replication/day",
        version="2.1.1",
    )
    url = reverse("admin:rivers_syncstate_change", args=[sync_state.pk])

    html = admin_client.get(url).text

    assert "OSM data as of" in html
    assert "ago)" in html
    assert "Sequence number: 4949" in html
    assert "Feed: https://planet.openstreetmap.org/replication/day" in html
    assert "osm2pgsql version: 2.1.1" in html


def test_sync_state_survives_replication_not_being_set_up(admin_client, sync_state):
    url = reverse("admin:rivers_syncstate_change", args=[sync_state.pk])

    assert admin_client.get(url).status_code == HTTPStatus.OK


def test_sync_state_lists_the_osm_schemas_and_what_they_are(admin_client, sync_state):
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA osm_retired")
        cursor.execute("CREATE SCHEMA osm_backup_20260813")
        cursor.execute("CREATE TABLE osm_backup_20260813.t AS SELECT 1 AS n")
    url = reverse("admin:rivers_syncstate_change", args=[sync_state.pk])

    html = admin_client.get(url).text

    assert "osm: " in html
    assert "live, what the site serves" in html
    assert "osm_retired: 0\xa0bytes (the previous rebuild" in html
    assert "osm_backup_20260813: " in html
    assert "not created by the pipeline" in html
