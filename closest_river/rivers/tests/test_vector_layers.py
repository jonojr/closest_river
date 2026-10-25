"""Tile rendering: zoom tiers, the merged stream wash, and caching."""

import mercantile
import pytest
from django.contrib.gis.geos import LineString

from closest_river.rivers.models import RiverSection
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tests.factories import RiverSectionFactory
from closest_river.rivers.vector_layers import RiverSectionVectorLayer
from closest_river.rivers.vector_layers import StreamSectionVectorLayer
from closest_river.rivers.vector_layers import css_pixel_size_metres
from closest_river.rivers.vector_layers import snap_size_metres

pytestmark = pytest.mark.django_db

# Melbourne, matching where the section factory builds its geometry.
SEED_LON, SEED_LAT = 144.92, -37.82
# The same place in Web Mercator.
SEED_X, SEED_Y = 16132000, -4554000


# Long enough to be several pixels across even at zoom 0, where one pixel is
# 156 km. Anything shorter is legitimately invisible when zoomed right out.
SEED_LENGTH_M = 600_000


def _tile_row(
    osm_way_id,
    waterway,
    offset=0,
    length=SEED_LENGTH_M,
    *,
    intermittent=False,
):
    return WaterwayTile.objects.create(
        osm_way_id=osm_way_id,
        waterway=waterway,
        intermittent=intermittent,
        geometry=LineString(
            (SEED_X + offset, SEED_Y),
            (SEED_X + offset + length, SEED_Y - length),
            srid=3857,
        ),
    )


def test_a_css_pixel_is_measured_against_a_512_unit_tile():
    """MapLibre renders vector tiles across 512 CSS pixels, not 256.

    Assuming 256 made every grid twice as coarse as intended, which showed up
    as right-angled staircases in the rendered stream network.
    """
    assert css_pixel_size_metres(0) == pytest.approx(78271.5, rel=1e-3)
    assert css_pixel_size_metres(8) == pytest.approx(css_pixel_size_metres(7) / 2)


def test_the_snap_grid_is_sub_pixel_where_channels_are_separable():
    """At zoom 5 and up the grid must be finer than a device pixel.

    A coarser grid is visible as a lattice, because snapping moves vertices
    onto rectangular grid points rather than just dropping them.
    """
    for zoom in range(5, 15):
        assert snap_size_metres(zoom) <= css_pixel_size_metres(zoom) / 2

    # Wider than that, a tile spans a continent and the fine grid triples its
    # size for a lattice nobody can resolve.
    assert snap_size_metres(4) == pytest.approx(css_pixel_size_metres(4))


def test_cached_zooms_read_the_generalised_tile_table():
    """Low zoom must never touch the full-resolution table.

    At planet scale a zoom 0 query against waterway_section would match every
    row in the database with no generalisation at all.
    """
    sql = RiverSectionVectorLayer()._cached_zoom_sql()

    assert "waterway_tile" in sql
    assert "waterway_section" not in sql


def test_uncached_zooms_read_the_canonical_table():
    sql = RiverSectionVectorLayer()._full_detail_sql()

    assert 'FROM "osm"."waterway_section"' in sql
    # Only joined, for the intermittent flag; see the test below.
    assert 'FROM "osm"."waterway_tile"' not in sql


def test_geometry_is_snapped_to_the_pixel_grid_at_every_zoom():
    """Sub-pixel vertices are invisible at any zoom, and MapLibre fetches a
    fresh tile per zoom, so there is no overzoom to reveal the quantisation.
    Leaving high zooms unsnapped made the densest z10 tile 4x larger.
    """
    layer = RiverSectionVectorLayer()

    assert "ST_SnapToGrid" in layer._cached_zoom_sql()
    assert "ST_SnapToGrid" in layer._full_detail_sql()


def test_streams_are_merged_when_cached_but_not_when_zoomed_in():
    layer = StreamSectionVectorLayer()

    assert "ST_LineMerge" in layer._cached_zoom_sql()
    assert "osm_way_id" not in layer._cached_zoom_sql(), (
        "a merged geometry has no single way id to attach"
    )
    # Zoomed in, individual streams become clickable again.
    assert "osm_way_id" in layer._full_detail_sql()


def test_rivers_keep_their_ids_at_every_zoom():
    """The popup lookup needs osm_way_id, so rivers are never merged."""
    layer = RiverSectionVectorLayer()

    assert "ST_LineMerge" not in layer._cached_zoom_sql()
    assert "osm_way_id" in layer._cached_zoom_sql()
    assert "osm_way_id" in layer._full_detail_sql()


def test_each_layer_only_draws_its_own_waterway_type():
    _tile_row(1, "river")
    _tile_row(2, "stream", offset=8000)
    tile = mercantile.tile(SEED_LON, SEED_LAT, 8)

    rivers = RiverSectionVectorLayer().get_tile(tile.x, tile.y, tile.z)
    streams = StreamSectionVectorLayer().get_tile(tile.x, tile.y, tile.z)

    assert rivers, "expected the river to render"
    assert streams, "expected the stream wash to render"
    assert rivers != streams


@pytest.mark.parametrize("zoom", [0, 2, 4, 6, 8, 10])
def test_the_stream_wash_survives_zooming_out(zoom):
    """The whole point: streams must still be there at low zoom.

    They used to be dropped entirely below zoom 9, which lost the dendritic
    texture that makes the map worth looking at.
    """
    _tile_row(1, "stream")

    tile = mercantile.tile(SEED_LON, SEED_LAT, zoom)
    assert StreamSectionVectorLayer().get_tile(tile.x, tile.y, zoom), (
        f"no stream wash at z{zoom}"
    )


def test_tiles_below_the_threshold_are_cached():
    _tile_row(1, "river")
    layer = RiverSectionVectorLayer()
    tile = mercantile.tile(SEED_LON, SEED_LAT, 6)

    first = layer.get_tile(tile.x, tile.y, tile.z)
    assert first

    # Remove the underlying row: the cached tile must still be served.
    WaterwayTile.objects.all().delete()
    assert layer.get_tile(tile.x, tile.y, tile.z) == first


def test_tiles_above_the_threshold_are_not_cached():
    section = RiverSectionFactory(osm_way_id=800001, waterway="river")
    layer = RiverSectionVectorLayer()

    lon, lat = section.geometry.coords[0]
    tile = mercantile.tile(lon, lat, 14)
    assert layer.get_tile(tile.x, tile.y, tile.z)

    RiverSection.objects.all().delete()
    assert layer.get_tile(tile.x, tile.y, tile.z) == b""


def test_sub_pixel_features_drop_out_when_zoomed_far_enough_out():
    """Snapping generalises: a feature finer than a pixel collapses away.

    This is the intended trade and it is safe, because snapping is per-vertex
    and so consistent across ways that share a node — a long chain of short
    ways keeps its overall shape, it just loses the segments that fall inside
    a single pixel.
    """
    _tile_row(1, "stream", length=3000)
    layer = StreamSectionVectorLayer()

    close = mercantile.tile(SEED_LON, SEED_LAT, 10)
    assert layer.get_tile(close.x, close.y, close.z), "3 km is visible at z10"

    far = mercantile.tile(SEED_LON, SEED_LAT, 0)
    assert layer.get_tile(far.x, far.y, far.z) == b"", (
        "3 km is a fiftieth of a pixel at z0"
    )


def test_empty_area_renders_an_empty_tile():
    layer = StreamSectionVectorLayer()
    # Middle of the Pacific.
    tile = mercantile.tile(-140.0, 0.0, 6)

    assert layer.get_tile(tile.x, tile.y, tile.z) == b""


def test_merged_streams_stay_split_by_intermittent():
    """Merging must not mix permanent and intermittent water into one
    feature, or the map can no longer draw them differently."""
    assert "GROUP BY intermittent" in StreamSectionVectorLayer()._cached_zoom_sql()


@pytest.mark.parametrize(
    "layer_class",
    [RiverSectionVectorLayer, StreamSectionVectorLayer],
)
def test_every_zoom_carries_the_intermittent_flag(layer_class):
    layer = layer_class()

    # Cached zooms use the precomputed column; they must not read the tags,
    # which live in waterway_section.
    assert "intermittent" in layer._cached_zoom_sql()
    assert "tags" not in layer._cached_zoom_sql()
    assert "intermittent" in layer._full_detail_sql()


@pytest.mark.parametrize("zoom", [6, 12])
def test_intermittent_reaches_the_rendered_tile(zoom):
    _tile_row(1, "stream", intermittent=True)
    RiverSectionFactory(
        osm_way_id=800010,
        waterway="stream",
        tags={"waterway": "stream", "intermittent": "yes"},
        geometry=LineString(
            (SEED_LON, SEED_LAT),
            (SEED_LON + 0.01, SEED_LAT - 0.01),
            srid=4326,
        ),
    )
    _tile_row(800010, "stream", intermittent=True)
    tile = mercantile.tile(SEED_LON, SEED_LAT, zoom)

    rendered = StreamSectionVectorLayer().get_tile(tile.x, tile.y, zoom)

    # MVT stores property names verbatim in the layer's key table.
    assert b"intermittent" in rendered


def test_full_detail_takes_the_flag_from_the_tile_table_not_the_tags():
    """For a relation member the flag is its river's verdict, which only
    waterway_tile holds; the way's own tags would break the river up again."""
    layer = RiverSectionVectorLayer()

    assert "waterway_tile" in layer._full_detail_sql()
    assert "tags" not in layer._full_detail_sql()
