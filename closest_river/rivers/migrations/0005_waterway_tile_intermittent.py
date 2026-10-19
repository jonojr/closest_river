"""Add waterway_tile.intermittent to an existing import.

waterways.lua now writes this column, so the next full rebuild creates it
like any other. This migration exists for the import already in place: the
new Lua style cannot append to a table without the column, and the tile
queries read it, so waiting for the monthly rebuild would break both the sync
and the map in the meantime.

Skipped where the table does not exist yet (a fresh database, or the test
suite, whose fixture creates the schema itself); the first import then
creates the column from the Lua style.
"""

from django.conf import settings
from django.db import migrations
from django.db.models import F

SCHEMA = settings.OSM_SCHEMA

# models.is_intermittent() as SQL. A copy rather than an import: migrations
# must keep doing what they did when written.
ADD_AND_BACKFILL = f"""
DO $$
BEGIN
    IF to_regclass('"{SCHEMA}".waterway_tile') IS NOT NULL THEN
        ALTER TABLE "{SCHEMA}".waterway_tile
            ADD COLUMN IF NOT EXISTS intermittent boolean NOT NULL DEFAULT false;
        UPDATE "{SCHEMA}".waterway_tile t
            SET intermittent = true
            FROM "{SCHEMA}".waterway_section s
            WHERE s.osm_way_id = t.osm_way_id
              AND (s.tags->>'intermittent' = 'yes'
                   OR s.tags->>'seasonal' IN ('yes', 'wet_season', 'wet'));
    END IF;
END $$;
"""  # noqa: S608

DROP = f"""
DO $$
BEGIN
    IF to_regclass('"{SCHEMA}".waterway_tile') IS NOT NULL THEN
        ALTER TABLE "{SCHEMA}".waterway_tile DROP COLUMN IF EXISTS intermittent;
    END IF;
END $$;
"""


def strand_cached_tiles(apps, schema_editor):
    """Cached tiles predate the new attribute; bump the version they key on.

    Only an existing row: without one no tile has been served, so there is
    nothing cached to strand.
    """
    SyncState = apps.get_model("rivers", "SyncState")
    SyncState.objects.filter(pk=1).update(data_version=F("data_version") + 1)


class Migration(migrations.Migration):
    dependencies = [
        ("rivers", "0004_osm2pgsql_schema"),
    ]

    operations = [
        migrations.RunSQL(sql=ADD_AND_BACKFILL, reverse_sql=DROP),
        migrations.RunPython(strand_cached_tiles, migrations.RunPython.noop),
    ]
