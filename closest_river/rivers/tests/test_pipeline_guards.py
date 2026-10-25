"""The checks that stop the pipeline destroying its own data.

An import that produced nothing was once swapped in over a good schema, and
because osm2pgsql --append creates any output table it finds missing, the daily
update then ran happily against the empty schema that replaced it — leaving the
site serving only the handful of waterways that had been edited since. Both
steps now refuse to run against a schema with no waterways in it.
"""

import pytest
from django.db import connection

from closest_river.rivers import osm_pipeline

pytestmark = pytest.mark.django_db(transaction=True)

LIVE = "osm_guard"
STAGING = f"{LIVE}_staging"
RETIRED = f"{LIVE}_retired"


def _execute(*statements: str) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _create_schema(schema: str, *, sections: int) -> None:
    """A schema shaped like an osm2pgsql one, with `sections` waterways in it."""
    _execute(
        f'CREATE SCHEMA "{schema}"',
        f'CREATE TABLE "{schema}"."{osm_pipeline.CANONICAL_TABLE}" '
        "(osm_way_id bigint primary key)",
        f'INSERT INTO "{schema}"."{osm_pipeline.CANONICAL_TABLE}" '
        f"SELECT generate_series(1, {sections})",
    )


@pytest.fixture(autouse=True)
def _clean_schemas():
    names = (LIVE, STAGING, RETIRED)
    _execute(*(f'DROP SCHEMA IF EXISTS "{name}" CASCADE' for name in names))
    yield
    _execute(*(f'DROP SCHEMA IF EXISTS "{name}" CASCADE' for name in names))


def _section_count(schema: str) -> int:
    with connection.cursor() as cursor:
        return osm_pipeline._section_count(cursor, schema)


def test_an_empty_staging_schema_is_not_promoted():
    _create_schema(LIVE, sections=500)
    _create_schema(STAGING, sections=0)

    with pytest.raises(osm_pipeline.OsmPipelineError, match="is empty"):
        osm_pipeline.swap_schema(staging=STAGING, live=LIVE)

    assert _section_count(LIVE) == 500, "the live data must survive a bad import"


def test_a_staging_schema_with_no_tables_is_not_promoted():
    """An import that never wrote a table at all — how the data was lost."""
    _create_schema(LIVE, sections=500)
    _execute(f'CREATE SCHEMA "{STAGING}"')

    with pytest.raises(osm_pipeline.OsmPipelineError, match="does not exist"):
        osm_pipeline.swap_schema(staging=STAGING, live=LIVE)

    assert _section_count(LIVE) == 500


def test_a_failed_swap_leaves_the_retired_copy_alone():
    """The retired schema is the only way back, and it is dropped by a swap."""
    _create_schema(LIVE, sections=500)
    _create_schema(RETIRED, sections=400)
    _create_schema(STAGING, sections=0)

    with pytest.raises(osm_pipeline.OsmPipelineError):
        osm_pipeline.swap_schema(staging=STAGING, live=LIVE)

    assert _section_count(RETIRED) == 400


def test_a_populated_staging_schema_is_promoted():
    _create_schema(LIVE, sections=500)
    _create_schema(STAGING, sections=600)

    osm_pipeline.swap_schema(staging=STAGING, live=LIVE)

    assert _section_count(LIVE) == 600
    assert _section_count(RETIRED) == 500


def test_replication_will_not_run_against_an_empty_schema(monkeypatch):
    """osm2pgsql would otherwise create the missing tables and report success."""
    _create_schema(LIVE, sections=0)
    monkeypatch.setattr(
        osm_pipeline,
        "_run",
        lambda command, *, description: pytest.fail("should not have run"),
    )

    with pytest.raises(osm_pipeline.OsmPipelineError, match="is empty"):
        osm_pipeline.replication_update(schema=LIVE)
