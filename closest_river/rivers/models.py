"""Models over the osm2pgsql-managed `osm` schema.

These tables are created and maintained by osm2pgsql using the flex style at
compose/production/osm2pgsql/waterways.lua, so every model here is
``managed = False``. Django owns the *shape of the queries*, not the shape of
the schema: to change a column, change the Lua style and re-import.

The `osm` schema is on the connection's search_path (see
config/settings/base.py), so bare table names resolve without qualification.
"""

from django.contrib.gis.db.models import LineStringField
from django.contrib.postgres.fields import ArrayField
from django.db import models


class SyncState(models.Model):
    """Singleton row tracking the state of the OSM pipeline.

    Unlike everything else in this module this table is Django-managed and
    lives in `public`. ``data_version`` is mixed into every tile cache key: a
    full rebuild bumps it, which strands every previously cached tile at once.
    Incremental syncs do *not* bump it — they evict individual tiles instead
    (see tile_cache.invalidate_expired_tiles).
    """

    SINGLETON_PK = 1

    data_version = models.PositiveIntegerField(default=1)
    last_full_import_at = models.DateTimeField(blank=True, null=True)
    last_sync_at = models.DateTimeField(blank=True, null=True)
    last_sync_tiles_expired = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "OSM sync state"

    def __str__(self):
        return f"data version {self.data_version}, last synced {self.last_sync_at}"

    @classmethod
    def load(cls) -> "SyncState":
        state, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return state


class RiverSection(models.Model):
    """A single OSM way forming part of a river or stream.

    Usually a way tagged waterway=river or waterway=stream, but also one that is
    only kept because it is a member of a river or stream *relation* — a traced
    watercourse routinely switches to canal or drain partway through, and
    dropping those segments puts a gap in the middle of a named river.
    """

    osm_way_id = models.BigIntegerField(primary_key=True)
    name = models.TextField(blank=True, null=True)  # noqa: DJ001
    # Always "river" or "stream": the class this way is drawn and queried as,
    # which for a promoted member is its relation's rather than its own. The
    # literal OSM tag is always in ``tags`` — see waterways.lua.
    waterway = models.TextField()
    # Denormalised most-significant role from the parent relation
    # (main_stream, side_stream, ...). RiverMembership holds every membership.
    role = models.TextField(blank=True, null=True)  # noqa: DJ001
    node_ids = ArrayField(models.BigIntegerField(), blank=True, null=True)
    tags = models.JSONField(default=dict, blank=True)
    geometry = LineStringField(srid=4326)
    river = models.ForeignKey(
        "River",
        to_field="osm_id",
        db_column="river_osm_id",
        # osm2pgsql writes way rows before relation rows, and legitimately
        # writes sections whose parent relation is outside the extract, so a
        # real foreign key constraint would abort the import.
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name="sections",
    )

    class Meta:
        managed = False
        db_table = "waterway_section"

    def __str__(self):
        return f"{self.name} ({self.osm_way_id})" if self.name else str(self.osm_way_id)


class River(models.Model):
    """An OSM type=waterway relation grouping many sections."""

    osm_id = models.BigIntegerField(primary_key=True)
    name = models.TextField(blank=True, null=True)  # noqa: DJ001
    destination = models.TextField(blank=True, null=True)  # noqa: DJ001
    wikipedia = models.TextField(blank=True, null=True)  # noqa: DJ001
    tags = models.JSONField(default=dict, blank=True)

    class Meta:
        managed = False
        db_table = "waterway_relation"

    def __str__(self):
        return f"{self.name} ({self.osm_id})" if self.name else str(self.osm_id)


class RiverMembership(models.Model):
    """Source of truth for relation membership.

    One row per way member per relation, so a way belonging to several
    relations is represented faithfully. ``sequence`` preserves member order,
    which is what any along-the-river distance calculation needs.
    """

    # osm2pgsql cannot generate a surrogate key, so the model is keyed on the
    # pair the importer already guarantees to be unique: sequence is assigned
    # per relation in waterways.lua, and relation ids are unique. waterways.lua
    # backs this with a unique index on the same two columns.
    pk = models.CompositePrimaryKey("river", "sequence")
    river = models.ForeignKey(
        River,
        to_field="osm_id",
        db_column="river_osm_id",
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name="memberships",
    )
    way_osm_id = models.BigIntegerField()
    role = models.TextField(blank=True, null=True)  # noqa: DJ001
    sequence = models.IntegerField()

    class Meta:
        managed = False
        db_table = "waterway_membership"
        # Deliberately no default ordering: Meta.ordering leaks into GROUP BY
        # and silently breaks .distinct() and aggregates, and would add a sort
        # to every incidental query over a large table. Order explicitly by
        # ("river_id", "sequence") where member order actually matters.

    def __str__(self):
        return f"{self.river_id}/{self.way_osm_id} ({self.role or 'no role'})"


class WaterwayTile(models.Model):
    """Web Mercator copy of every waterway, used for the cached zoom levels.

    Web Mercator because osm2pgsql only permits tile expiry on 3857 geometry
    columns — see waterways.lua. Maintained by the same append that updates
    RiverSection, so it cannot drift out of step with it.

    Includes streams, not just rivers: the dense dendritic network is the
    point of the map and has to survive zooming out. Keeping it affordable is
    a tile-time concern (snap to a pixel grid, merge, small extent) rather than
    something solved by throwing features away — see vector_layers.py.
    """

    osm_way_id = models.BigIntegerField(primary_key=True)
    waterway = models.TextField()
    geometry = LineStringField(srid=3857)

    class Meta:
        managed = False
        db_table = "waterway_tile"

    def __str__(self):
        return f"{self.waterway} {self.osm_way_id}"
