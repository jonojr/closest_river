"""Test scaffolding for the osm2pgsql-owned tables.

The waterway models are ``managed = False``, so Django's test runner does not
create their tables. This module creates them, mirroring exactly what
compose/production/osm2pgsql/waterways.lua produces.

**If you change waterways.lua, change this DDL too.** test_osm_pipeline.py
guards against the two drifting apart by running the real importer and
comparing the resulting columns against the models, but it is skipped wherever
osm2pgsql is not installed.
"""

import pytest
from django.core.cache import cache
from django.db import connection

OSM_SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS osm;

CREATE TABLE IF NOT EXISTS osm.waterway_relation (
    osm_id bigint PRIMARY KEY,
    name text,
    destination text,
    wikipedia text,
    tags jsonb
);

CREATE TABLE IF NOT EXISTS osm.waterway_section (
    osm_way_id bigint PRIMARY KEY,
    name text,
    waterway text NOT NULL,
    role text,
    river_osm_id bigint,
    node_ids bigint[],
    tags jsonb,
    geometry geometry(LineString, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS osm.waterway_membership (
    river_osm_id bigint NOT NULL,
    way_osm_id bigint NOT NULL,
    role text,
    sequence integer NOT NULL
);

-- RiverMembership's composite primary key. osm2pgsql builds a unique index
-- rather than a primary key constraint, so mirror that and not a PRIMARY KEY.
CREATE UNIQUE INDEX IF NOT EXISTS waterway_membership_river_osm_id_sequence_idx
    ON osm.waterway_membership (river_osm_id, sequence);

CREATE TABLE IF NOT EXISTS osm.waterway_tile (
    osm_way_id bigint PRIMARY KEY,
    waterway text NOT NULL,
    intermittent boolean NOT NULL DEFAULT false,
    geometry geometry(LineString, 3857) NOT NULL
);

CREATE TABLE IF NOT EXISTS osm.tile_expire (
    zoom integer NOT NULL,
    x integer NOT NULL,
    y integer NOT NULL,
    first timestamp with time zone DEFAULT CURRENT_TIMESTAMP(0),
    last timestamp with time zone DEFAULT CURRENT_TIMESTAMP(0),
    PRIMARY KEY (zoom, x, y)
);
"""


OSM_TABLES = (
    "waterway_section",
    "waterway_relation",
    "waterway_membership",
    "waterway_tile",
    "tile_expire",
)


@pytest.fixture(scope="session", autouse=True)
def _create_osm_tables(django_db_setup, django_db_blocker):
    """Create the unmanaged waterway tables once per test session.

    Dropped first rather than truncated, because pytest runs with --reuse-db:
    that both empties them (Django's flush between transactional tests only
    knows about managed tables, so rows committed by a previous run would
    otherwise survive and quietly break later assertions) and picks up a change
    to the DDL below without anyone having to remember to drop their test
    database.
    """
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS osm")
        tables = ", ".join(f"osm.{table}" for table in OSM_TABLES)
        cursor.execute(f"DROP TABLE IF EXISTS {tables}")
        cursor.execute(OSM_SCHEMA_DDL)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Keep cached tiles and data versions from leaking between tests."""
    cache.clear()
    yield
    cache.clear()
