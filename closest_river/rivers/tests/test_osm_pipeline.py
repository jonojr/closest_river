"""Integration tests that drive the real osm2pgsql importer.

These are the tests that actually guard the pipeline. They cover the cases that
made osm2pgsql the right tool in the first place — particularly a way whose
geometry changes because one of its *nodes* moved, while the way itself is
untouched by the diff. No amount of ogr2ogr could do that, and no unit test
with hand-inserted rows can prove it works.

Skipped where osm2pgsql is not installed, i.e. everywhere except the osmworker
image. CI runs them in a dedicated step.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

from closest_river.rivers import osm_pipeline
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        shutil.which("osm2pgsql") is None,
        reason="osm2pgsql is only installed in the osmworker image",
    ),
]

FIXTURE = Path(__file__).parent / "fixtures" / "waterways.osm"

# Kept separate from `osm` so these tests cannot disturb the tables the unit
# tests create, and so a failure leaves the developer's data alone.
SCHEMA = "osm_it"
MIDDLE_SCHEMA = "osm_it_middle"


def _dsn() -> str:
    db = connection.settings_dict
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )


def _osm2pgsql(*extra_args: str, source: Path) -> None:
    subprocess.run(  # noqa: S603
        [
            shutil.which("osm2pgsql"),
            *extra_args,
            "--slim",
            "--output=flex",
            f"--style={settings.OSM2PGSQL_STYLE}",
            f"--schema={SCHEMA}",
            f"--middle-schema={MIDDLE_SCHEMA}",
            "--database",
            _dsn(),
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _query(sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql.replace("{s}", SCHEMA), params or [])
        return cursor.fetchall()


def _scalar(sql: str, params=None):
    return _query(sql, params)[0][0]


def _geometry_wkt(way_osm_id: int) -> str:
    return _scalar(
        "SELECT ST_AsText(geometry) FROM {s}.waterway_section WHERE osm_way_id = %s",
        [way_osm_id],
    )


def _apply(tmp_path: Path, body: str, name: str = "change.osc") -> None:
    """Write an osmChange document and append it to the database."""
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osmChange version="0.6" generator="test">\n'
        f"{textwrap.dedent(body)}\n"
        "</osmChange>\n",
    )
    _osm2pgsql("--append", source=path)


@pytest.fixture
def imported():
    """A fresh --create import of the fixture, in a schema of its own."""
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        cursor.execute(f'DROP SCHEMA IF EXISTS "{MIDDLE_SCHEMA}" CASCADE')
        cursor.execute(f'CREATE SCHEMA "{SCHEMA}"')
        cursor.execute(f'CREATE SCHEMA "{MIDDLE_SCHEMA}"')

    _osm2pgsql("--create", source=FIXTURE)
    yield

    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        cursor.execute(f'DROP SCHEMA IF EXISTS "{MIDDLE_SCHEMA}" CASCADE')


def test_import_keeps_rivers_streams_and_promoted_members(imported):
    ways = dict(_query("SELECT osm_way_id, waterway FROM {s}.waterway_section"))

    assert ways == {100: "river", 101: "stream", 102: "river", 104: "river"}, (
        "a canal in a river relation must be kept and stored as a river, while "
        "a ditch whose only relation is a drain must be filtered out"
    )


def test_a_canal_in_a_river_relation_is_kept(imported):
    """The Bendigo Creek case: a mixed relation must not have a hole in it.

    Way 104 is tagged waterway=canal, so it is dropped by the tag filter in
    stage 1 and inserted only on the stage-2 pass that select_relation_members
    drives. That osm2pgsql reprocesses a way which produced no stage-1 row at
    all is the load-bearing assumption of the whole promotion mechanism, and
    this is what proves it.
    """
    row = _query(
        "SELECT waterway, tags->>'waterway', river_osm_id, role "
        "FROM {s}.waterway_section WHERE osm_way_id = 104",
    )

    assert row == [("river", "canal", 200, "main_stream")], (
        "promotion must normalise the waterway column to the relation's class "
        "while leaving the literal tag in tags"
    )


def test_a_ditch_in_a_drain_relation_is_not_promoted(imported):
    """Only river and stream relations promote their members.

    Way 103 is a ditch and a main_stream member of relation 201, but 201 is a
    waterway=drain relation, so 103 stays out of both tables.
    """
    assert (
        _scalar("SELECT count(*) FROM {s}.waterway_section WHERE osm_way_id = 103") == 0
    )
    assert _scalar("SELECT count(*) FROM {s}.waterway_tile WHERE osm_way_id = 103") == 0
    # The relation and its membership are still recorded, as they always were.
    assert (
        _scalar("SELECT count(*) FROM {s}.waterway_membership WHERE way_osm_id = 103")
        == 1
    )
    assert _scalar(
        "SELECT tags->>'waterway' FROM {s}.waterway_relation WHERE osm_id = 201",
    ) == ("drain")


def test_relation_membership_roles_and_order(imported):
    assert _scalar("SELECT name FROM {s}.waterway_relation WHERE osm_id = 200") == (
        "Test River System"
    )

    members = _query(
        "SELECT sequence, way_osm_id, role FROM {s}.waterway_membership "
        "WHERE river_osm_id = 200 ORDER BY sequence",
    )
    assert members == [
        (1, 100, "main_stream"),
        (2, 102, "side_stream"),
        (3, 104, "main_stream"),
    ]

    # ...and denormalised onto the sections for fast lookups.
    sections = _query(
        "SELECT osm_way_id, river_osm_id, role FROM {s}.waterway_section "
        "WHERE river_osm_id IS NOT NULL ORDER BY osm_way_id",
    )
    assert sections == [
        (100, 200, "main_stream"),
        (102, 200, "side_stream"),
        (104, 200, "main_stream"),
    ]


def test_node_ids_are_captured(imported):
    assert _scalar(
        "SELECT node_ids FROM {s}.waterway_section WHERE osm_way_id = 100",
    ) == [
        1,
        2,
        3,
    ]


def test_the_tile_table_carries_streams_as_well_as_rivers(imported):
    """The dendritic stream network has to survive zooming out.

    Tile weight is handled at render time (snap, merge, small extent), not by
    dropping streams from the tile table.
    """
    rows = dict(_query("SELECT osm_way_id, waterway FROM {s}.waterway_tile"))

    assert rows == {100: "river", 101: "stream", 102: "river", 104: "river"}


def test_the_tile_table_flags_intermittent_waterways(imported):
    """intermittent=yes and seasonal=* both count; untagged ways do not."""
    rows = dict(_query("SELECT osm_way_id, intermittent FROM {s}.waterway_tile"))

    assert rows == {100: False, 101: True, 102: True, 104: False}


def test_members_take_their_rivers_verdict_after_import(imported):
    """Way 102 is the only intermittent one of relation 200's three, so it is
    drawn permanent like the rest; stream 101 belongs to no relation."""
    restyled = osm_pipeline.apply_river_intermittency(SCHEMA, expire=False)

    rows = dict(_query("SELECT osm_way_id, intermittent FROM {s}.waterway_tile"))
    assert restyled == 1
    assert rows == {100: False, 101: True, 102: False, 104: False}


def test_tile_geometry_is_in_web_mercator(imported):
    srid = _scalar("SELECT ST_SRID(geometry) FROM {s}.waterway_tile LIMIT 1")
    assert srid == 3857, "tile expiry only works on Web Mercator columns"

    canonical_srid = _scalar(
        "SELECT ST_SRID(geometry) FROM {s}.waterway_section LIMIT 1",
    )
    assert canonical_srid == 4326, "ClosestRiver casts to geography for true metres"


def test_moving_a_node_updates_an_untouched_way_and_expires_tiles(imported, tmp_path):
    """The case that ruled ogr2ogr out.

    Way 100 is not mentioned in this diff at all; only node 2 moves. Its
    geometry must still be rebuilt, and the affected tiles expired.
    """
    before = _geometry_wkt(100)
    assert _scalar("SELECT count(*) FROM {s}.tile_expire") == 0

    _apply(
        tmp_path,
        """
        <modify>
         <node id="2" version="2" lat="-37.15" lon="145.25"/>
        </modify>
        """,
    )

    assert _geometry_wkt(100) != before

    expired = _query("SELECT zoom, x, y FROM {s}.tile_expire ORDER BY zoom")
    assert expired, "a geometry change must expire the tiles that contain it"
    zooms = [row[0] for row in expired]
    assert min(zooms) >= 1, "osm2pgsql cannot emit zoom 0; tile_cache handles it"
    # MAX_CACHED_ZOOM in waterways.lua, which must track CACHE_BELOW_ZOOM_LEVEL.
    assert max(zooms) == CACHE_BELOW_ZOOM_LEVEL - 1, (
        "every cached zoom must be expirable, and no more"
    )


def test_retagging_a_river_to_a_stream_moves_it_between_layers(imported, tmp_path):
    """A retag must restyle the way, not delete it.

    The two tile layers are split on this column, so it has to follow the tag
    through a diff or the way would be drawn as a river forever.
    """
    assert _scalar("SELECT waterway FROM {s}.waterway_tile WHERE osm_way_id = 102") == (
        "river"
    )

    _apply(
        tmp_path,
        """
        <modify>
         <way id="102" version="2">
          <nd ref="3"/>
          <nd ref="6"/>
          <tag k="waterway" v="stream"/>
          <tag k="name" v="Test Side Channel"/>
         </way>
        </modify>
        """,
    )

    assert _scalar(
        "SELECT waterway FROM {s}.waterway_section WHERE osm_way_id = 102",
    ) == ("stream")
    assert _scalar("SELECT waterway FROM {s}.waterway_tile WHERE osm_way_id = 102") == (
        "stream"
    )
    assert _scalar("SELECT count(*) FROM {s}.tile_expire") > 0


def test_deleting_a_way_removes_it_everywhere(imported, tmp_path):
    _apply(
        tmp_path,
        """
        <delete>
         <way id="100" version="2"/>
        </delete>
        """,
    )

    assert (
        _scalar("SELECT count(*) FROM {s}.waterway_section WHERE osm_way_id = 100") == 0
    )
    assert _scalar("SELECT count(*) FROM {s}.waterway_tile WHERE osm_way_id = 100") == 0


def test_adding_a_way_to_a_relation_links_the_section(imported, tmp_path):
    """select_relation_members is what makes this work.

    Way 101 is untouched by the diff; only the relation changes. Without
    select_relation_members its river_osm_id would stay NULL forever.
    """
    assert (
        _scalar("SELECT river_osm_id FROM {s}.waterway_section WHERE osm_way_id = 101")
        is None
    )

    _apply(
        tmp_path,
        """
        <modify>
         <relation id="200" version="2">
          <member type="way" ref="100" role="main_stream"/>
          <member type="way" ref="102" role="side_stream"/>
          <member type="way" ref="104" role="main_stream"/>
          <member type="way" ref="101" role="side_stream"/>
          <tag k="type" v="waterway"/>
          <tag k="waterway" v="river"/>
          <tag k="name" v="Test River System"/>
          <tag k="destination" v="Test Bay"/>
         </relation>
        </modify>
        """,
    )

    assert (
        _scalar("SELECT river_osm_id FROM {s}.waterway_section WHERE osm_way_id = 101")
        == 200
    )
    assert _scalar("SELECT role FROM {s}.waterway_section WHERE osm_way_id = 101") == (
        "side_stream"
    )
    assert (
        _scalar("SELECT count(*) FROM {s}.waterway_membership WHERE river_osm_id = 200")
        == 4
    )


def test_editing_a_promoted_way_alone_does_not_drop_it(imported, tmp_path):
    """The regression risk of promotion.

    A promoted way is only in the tables because of its relation, so if a diff
    that touches the way but not the relation did not reprocess the relation,
    the way would be deleted outright rather than merely losing its
    river_osm_id. Relation 200 is untouched here.
    """
    before = _geometry_wkt(104)

    _apply(
        tmp_path,
        """
        <modify>
         <node id="9" version="2" lat="-37.6" lon="145.8"/>
        </modify>
        """,
    )

    row = _query(
        "SELECT waterway, river_osm_id FROM {s}.waterway_section "
        "WHERE osm_way_id = 104",
    )
    assert row == [("river", 200)], "a promoted way must survive an edit of its own"
    assert _geometry_wkt(104) != before
    assert _scalar("SELECT waterway FROM {s}.waterway_tile WHERE osm_way_id = 104") == (
        "river"
    )


def test_removing_a_promoted_way_from_its_relation_drops_it(imported, tmp_path):
    """Demotion is the inverse of promotion and must not leave a stale row.

    Way 104 is a canal, so once relation 200 no longer claims it there is
    nothing keeping it in the data.
    """
    _apply(
        tmp_path,
        """
        <modify>
         <relation id="200" version="2">
          <member type="way" ref="100" role="main_stream"/>
          <member type="way" ref="102" role="side_stream"/>
          <tag k="type" v="waterway"/>
          <tag k="waterway" v="river"/>
          <tag k="name" v="Test River System"/>
          <tag k="destination" v="Test Bay"/>
         </relation>
        </modify>
        """,
    )

    assert (
        _scalar("SELECT count(*) FROM {s}.waterway_section WHERE osm_way_id = 104") == 0
    )
    assert _scalar("SELECT count(*) FROM {s}.waterway_tile WHERE osm_way_id = 104") == 0


def test_applying_the_same_diff_twice_is_idempotent(imported, tmp_path):
    diff = """
        <modify>
         <node id="2" version="2" lat="-37.15" lon="145.25"/>
        </modify>
    """
    _apply(tmp_path, diff, name="first.osc")
    once = _geometry_wkt(100)
    rows_once = _scalar("SELECT count(*) FROM {s}.waterway_section")

    _apply(tmp_path, diff, name="second.osc")

    assert _scalar("SELECT count(*) FROM {s}.waterway_section") == rows_once
    assert _geometry_wkt(100) == once


def test_the_importer_enforces_the_membership_key(imported):
    """RiverMembership is keyed on (river_osm_id, sequence).

    Django cannot create an index for an unmanaged table, so waterways.lua has
    to — and nothing else here would notice if it stopped.
    """
    definitions = [
        row[0]
        for row in _query(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'waterway_membership'",
            [SCHEMA],
        )
    ]

    assert any(
        "UNIQUE" in definition
        and "river_osm_id" in definition
        and "sequence" in definition
        for definition in definitions
    ), definitions


def test_importer_output_matches_the_django_models(imported):
    """Guard against models.py and waterways.lua drifting apart.

    Also covers the DDL in conftest.py, which mirrors the same tables by hand.
    """
    from closest_river.rivers.models import River
    from closest_river.rivers.models import RiverMembership
    from closest_river.rivers.models import RiverSection

    for model in (RiverSection, River, RiverMembership):
        expected = {field.column for field in model._meta.concrete_fields}
        actual = {
            row[0]
            for row in _query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                [SCHEMA, model._meta.db_table],
            )
        }
        assert expected <= actual, (
            f"{model.__name__} expects columns missing from the imported table: "
            f"{expected - actual}"
        )
