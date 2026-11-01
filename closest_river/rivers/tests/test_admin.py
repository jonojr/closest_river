"""The pipeline's tables are inspectable in the admin, and only inspectable."""

from http import HTTPStatus

import pytest
from django.contrib.gis.geos import LineString
from django.urls import reverse

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
