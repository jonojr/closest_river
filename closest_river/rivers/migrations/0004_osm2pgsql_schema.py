"""Hand the waterway tables over to osm2pgsql.

The rivers_river / rivers_riversection / rivers_osmdata tables are dropped and
replaced by unmanaged models over the `osm` schema, which osm2pgsql creates and
maintains from compose/production/osm2pgsql/waterways.lua.

This is destructive: the old tables held data loaded by the removed pyosmium
ingester and there is no migration path for it, because the new schema is keyed
on bigint OSM ids rather than the old CharField ones. Run
`full_import_osm_waterways` afterwards to repopulate.

Note that AlterModelOptions(managed=False) is deliberately NOT used here. It
would leave the legacy tables behind as orphans while the models silently
started pointing at new table names.
"""

import django.contrib.gis.db.models.fields
import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("rivers", "0003_alter_river_osm_id_alter_riversection_osm_way_id"),
    ]

    operations = [
        # osm2pgsql refuses to run against a schema that does not exist, and
        # the connection's search_path references it from the first query.
        migrations.RunSQL(
            sql=[
                'CREATE SCHEMA IF NOT EXISTS "osm"',
                'CREATE SCHEMA IF NOT EXISTS "osm_middle"',
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Drop the legacy tables.
        migrations.RemoveField(model_name="riversection", name="river"),
        migrations.DeleteModel(name="RiverSection"),
        migrations.DeleteModel(name="River"),
        migrations.DeleteModel(name="OsmData"),
        # Recreate as unmanaged models over the osm2pgsql-owned tables. These
        # CreateModel operations emit no DDL: they only teach Django's state
        # what the tables look like.
        migrations.CreateModel(
            name="River",
            fields=[
                ("osm_id", models.BigIntegerField(primary_key=True, serialize=False)),
                ("name", models.TextField(blank=True, null=True)),
                ("destination", models.TextField(blank=True, null=True)),
                ("wikipedia", models.TextField(blank=True, null=True)),
                ("tags", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "waterway_relation",
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="RiverSection",
            fields=[
                (
                    "osm_way_id",
                    models.BigIntegerField(primary_key=True, serialize=False),
                ),
                ("name", models.TextField(blank=True, null=True)),
                ("waterway", models.TextField()),
                ("role", models.TextField(blank=True, null=True)),
                (
                    "node_ids",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.BigIntegerField(),
                        blank=True,
                        null=True,
                        size=None,
                    ),
                ),
                ("tags", models.JSONField(blank=True, default=dict)),
                (
                    "geometry",
                    django.contrib.gis.db.models.fields.LineStringField(srid=4326),
                ),
                (
                    "river",
                    models.ForeignKey(
                        blank=True,
                        db_column="river_osm_id",
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="sections",
                        to="rivers.river",
                        to_field="osm_id",
                    ),
                ),
            ],
            options={
                "db_table": "waterway_section",
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="RiverMembership",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "river",
                        "sequence",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("way_osm_id", models.BigIntegerField()),
                ("role", models.TextField(blank=True, null=True)),
                ("sequence", models.IntegerField()),
                (
                    "river",
                    models.ForeignKey(
                        db_column="river_osm_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="memberships",
                        to="rivers.river",
                        to_field="osm_id",
                    ),
                ),
            ],
            options={
                "db_table": "waterway_membership",
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="WaterwayTile",
            fields=[
                (
                    "osm_way_id",
                    models.BigIntegerField(primary_key=True, serialize=False),
                ),
                ("waterway", models.TextField()),
                (
                    "geometry",
                    django.contrib.gis.db.models.fields.LineStringField(srid=3857),
                ),
            ],
            options={
                "db_table": "waterway_tile",
                "managed": False,
            },
        ),
        # Django-managed, unlike everything above.
        migrations.CreateModel(
            name="SyncState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("data_version", models.PositiveIntegerField(default=1)),
                ("last_full_import_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_tiles_expired", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "OSM sync state",
            },
        ),
    ]
