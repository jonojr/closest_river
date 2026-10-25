"""Read-path behaviour for the closest-river lookup and popup."""

import pytest
from django.contrib.gis.geos import LineString
from rest_framework.test import APIRequestFactory

from closest_river.rivers.api.views import ClosestRiver
from closest_river.rivers.api.views import RiverGeo
from closest_river.rivers.api.views import RiverSectionPopupInfo
from closest_river.rivers.tests.factories import RiverFactory
from closest_river.rivers.tests.factories import RiverSectionFactory

pytestmark = pytest.mark.django_db

# Somewhere in Melbourne; the factory geometries are built around here.
LAT, LON = "-37.81", "144.91"


def _closest(lat=LAT, lon=LON, **params):
    request = APIRequestFactory().get("/", params)
    return ClosestRiver.as_view()(request, lat=lat, lon=lon)


def test_returns_the_nearest_river():
    river = RiverFactory(name="Yarra")
    RiverSectionFactory(osm_way_id=800000, name="Yarra bend", river=river)

    response = _closest()

    assert response.status_code == 200
    assert response.data["river"]["name"] == "Yarra"
    assert response.data["distance"] >= 0
    assert response.data["closest_point_on_river"]


def test_streams_are_never_the_closest_river():
    """A much nearer stream must lose to a further river.

    templates/pages/instructions.html tells users smaller water-courses are
    never highlighted, but the query previously had no waterway filter at all.
    """
    RiverSectionFactory(
        osm_way_id=800001,
        name="Tiny creek",
        waterway="stream",
        geometry=LineString((144.9101, -37.8101), (144.9102, -37.8102), srid=4326),
    )
    far_river = RiverSectionFactory(
        osm_way_id=800002,
        name="Big river",
        waterway="river",
        geometry=LineString((145.6, -38.4), (145.7, -38.5), srid=4326),
    )

    response = _closest()

    assert response.status_code == 200
    assert response.data["section"]["osm_way_id"] == far_river.osm_way_id


def test_any_waterway_lets_a_nearer_stream_win():
    """The map's "any waterway" switch includes streams in the search."""
    stream = RiverSectionFactory(
        osm_way_id=800005,
        name="Tiny creek",
        waterway="stream",
        geometry=LineString((144.9101, -37.8101), (144.9102, -37.8102), srid=4326),
    )
    RiverSectionFactory(
        osm_way_id=800006,
        name="Big river",
        waterway="river",
        geometry=LineString((145.6, -38.4), (145.7, -38.5), srid=4326),
    )

    response = _closest(waterway="any")

    assert response.status_code == 200
    assert response.data["section"]["osm_way_id"] == stream.osm_way_id


def test_reports_whether_the_nearest_stretch_is_intermittent():
    """A way in no relation has only its own tags to go on."""
    RiverSectionFactory(
        osm_way_id=800007,
        tags={"waterway": "river", "intermittent": "yes"},
    )

    response = _closest()

    assert response.status_code == 200
    assert response.data["intermittent"] is True


def test_a_member_reports_its_rivers_verdict_not_its_own():
    """The card must agree with the map, which draws a river all one way."""
    river = RiverFactory()
    RiverSectionFactory(
        osm_way_id=800008,
        river=river,
        tags={"waterway": "river", "intermittent": "yes"},
    )
    for osm_way_id in (800050, 800051):
        RiverSectionFactory(osm_way_id=osm_way_id, river=river)

    response = _closest()

    assert response.data["section"]["osm_way_id"] == 800008
    assert response.data["intermittent"] is False


def test_river_geometry_carries_the_rivers_verdict():
    river = RiverFactory(tags={"type": "waterway", "intermittent": "yes"})
    RiverSectionFactory(river=river)
    request = APIRequestFactory().get("/")

    response = RiverGeo.as_view()(request, osm_id=river.osm_id)

    assert response.data["intermittent"] is True


def test_no_data_returns_404_rather_than_a_500():
    """Previously this dereferenced None and raised AttributeError."""
    response = _closest()

    assert response.status_code == 404


def test_section_without_a_parent_river_still_resolves():
    section = RiverSectionFactory(osm_way_id=800003, river=None)

    response = _closest()

    assert response.status_code == 200
    assert response.data["section"]["osm_way_id"] == section.osm_way_id
    assert response.data["geometry"]


def test_popup_merges_river_tags_under_section_tags():
    river = RiverFactory(name="Yarra", tags={"name": "Yarra", "wikipedia": "en:Yarra"})
    section = RiverSectionFactory(
        osm_way_id=800004,
        river=river,
        tags={"waterway": "river", "boat": "yes"},
    )

    response = RiverSectionPopupInfo.as_view()(
        APIRequestFactory().get("/"),
        osm_way_id=section.osm_way_id,
    )

    assert response.status_code == 200
    assert response.data["Name"]["text"] == "Yarra"
    assert response.data["Name"]["link"] == f"/rivers/{river.osm_id}/"
    assert (
        response.data["Wikipedia"]["link"] == "https://en.wikipedia.org/wiki/en:Yarra"
    )
    assert response.data["boat"]["text"] == "yes"
