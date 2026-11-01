from itertools import groupby

from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.template.defaultfilters import filesizeformat
from django.utils.formats import date_format
from django.utils.html import format_html_join
from django.utils.timesince import timesince
from django.utils.timezone import localtime

from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection
from closest_river.rivers.models import SyncState
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL
from closest_river.rivers.vector_layers import TILE_BUFFER
from closest_river.rivers.vector_layers import TILE_EXTENT

# Past this many tiles at one zoom, the rest are summarised as a count.
_MAX_TILES_LISTED = 20


class ReadOnlyOsmAdmin(admin.ModelAdmin):
    """Base for the osm2pgsql-owned tables.

    These rows are rewritten by every replication sync, so any edit made here
    would be silently reverted. Expose them for inspection only.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RiverSection)
class RiverSectionAdmin(ReadOnlyOsmAdmin):
    list_display = ("osm_way_id", "name", "waterway", "role", "river")
    list_filter = ("waterway", "role")
    search_fields = ("name",)


@admin.register(River)
class RiverAdmin(ReadOnlyOsmAdmin):
    list_display = ("osm_id", "name", "destination")
    search_fields = ("name",)


@admin.register(WaterwayTile)
class WaterwayTileAdmin(ReadOnlyOsmAdmin):
    """Where to check how the map will draw a way at the cached zooms: the
    intermittent flag here is the river's verdict, not the way's own tags.
    """

    list_display = ("osm_way_id", "waterway", "intermittent")
    list_filter = ("waterway", "intermittent")
    search_fields = ("=osm_way_id",)
    readonly_fields = ("cached_tiles",)

    @admin.display(description="Cached tiles it is drawn in")
    def cached_tiles(self, obj):
        """Every cached-zoom tile the way is drawn in, as (x, y) per zoom.

        These are the cache entries an edit to this way evicts.
        """
        rows = groupby(_tiles_crossed(obj.osm_way_id), key=lambda tile: tile[0])
        return format_html_join(
            "",
            "<div>z{}: {}</div>",
            ((z, _describe_tiles([(x, y) for _, x, y in tiles])) for z, tiles in rows),
        )


@admin.register(SyncState)
class SyncStateAdmin(ReadOnlyOsmAdmin):
    """The pipeline's own bookkeeping. Django-managed rather than osm2pgsql's,
    but just as read-only: only the sync and import tasks write it, and bumping
    data_version by hand would strand the whole tile cache.

    Shown alongside what osm2pgsql records about the import and replication,
    and the OSM schemas on disk, which are not models but are where a stalled
    feed or a leftover copy would show up.
    """

    list_display = (
        "osm_data_as_of",
        "last_sync_at",
        "cache_keys_evicted",
        "last_full_import_at",
        "data_version",
    )
    fields = (*list_display, "replication", "osm_schemas")

    @admin.display(description="OSM data as of")
    def osm_data_as_of(self, obj):
        """The newest OSM edit applied. If this falls well behind the last
        sync, the sync is running but the replication feed is not advancing.
        """
        stamp = osm_pipeline.replication_timestamp()
        if stamp is None:
            return None
        when = date_format(localtime(stamp), "DATETIME_FORMAT")
        return f"{when} ({timesince(stamp)} ago)"

    @admin.display(description="Cache keys evicted by the last sync")
    def cache_keys_evicted(self, obj):
        # One key per tile per layer, which the field name does not say.
        return obj.last_sync_tiles_expired

    @admin.display(description="Replication")
    def replication(self, obj):
        properties = osm_pipeline.replication_properties()
        if not properties:
            return None
        rows = [
            ("Sequence number", properties.get("replication_sequence_number")),
            ("Feed", properties.get("replication_base_url")),
            ("Imported file's timestamp", properties.get("import_timestamp")),
            ("osm2pgsql version", properties.get("version")),
        ]
        return format_html_join(
            "",
            "<div>{}: {}</div>",
            ((label, value) for label, value in rows if value),
        )

    @admin.display(description="OSM schemas on disk")
    def osm_schemas(self, obj):
        return format_html_join(
            "",
            "<div>{}: {} ({})</div>",
            (
                (name, filesizeformat(size), _schema_role(name))
                for name, size in osm_pipeline.schema_sizes()
            ),
        )


def _schema_role(name: str) -> str:
    live = settings.OSM_SCHEMA
    roles = {
        live: "live, what the site serves",
        settings.OSM_MIDDLE_SCHEMA: "osm2pgsql's middle tables, for replication",
        f"{live}_staging": "a rebuild in progress, or left behind by one that failed",
        f"{live}_retired": "the previous rebuild, kept to roll back to until the next",
    }
    return roles.get(name, "not created by the pipeline")


def _tiles_crossed(osm_way_id: int) -> list[tuple[int, int, int]]:
    """(z, x, y) of every cached-zoom tile a waterway is drawn in.

    Tested against the line itself, so a diagonal river does not list tiles
    it never enters, but with each tile widened by its buffer: a line just
    outside a tile is still drawn in that buffer, as _tiles_covering_sql in
    osm_pipeline allows for when it expires tiles. The bounding box narrows
    each zoom to a range of candidate tiles first.
    """
    table = f'"{settings.OSM_SCHEMA}"."waterway_tile"'
    half = osm_pipeline.MERCATOR_HALF_WORLD_M
    size = f"({2 * half} / (1 << z))"
    pad = TILE_BUFFER / TILE_EXTENT
    last = "((1 << z) - 1)"

    def bounds(low: str, high: str) -> tuple[str, str]:
        return (
            f"GREATEST(floor({low} / {size} - {pad})::int, 0)",
            f"LEAST(floor({high} / {size} + {pad})::int, {last})",
        )

    x0, x1 = bounds(f"(ST_XMin(g) + {half})", f"(ST_XMax(g) + {half})")
    y0, y1 = bounds(f"({half} - ST_YMax(g))", f"({half} - ST_YMin(g))")
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH way AS (SELECT geometry AS g FROM {table} WHERE osm_way_id = %s)
            SELECT z, x, y
            FROM way,
                 generate_series(0, %s) AS z,
                 LATERAL generate_series({x0}, {x1}) AS x,
                 LATERAL generate_series({y0}, {y1}) AS y
            WHERE ST_Intersects(g, ST_TileEnvelope(z, x, y, margin => {pad}))
            ORDER BY z, x, y
            """,  # noqa: S608
            [osm_way_id, CACHE_BELOW_ZOOM_LEVEL - 1],
        )
        return cursor.fetchall()


def _describe_tiles(tiles: list[tuple[int, int]]) -> str:
    listed = ", ".join(f"({x}, {y})" for x, y in tiles[:_MAX_TILES_LISTED])
    hidden = len(tiles) - _MAX_TILES_LISTED
    return f"{listed} and {hidden} more" if hidden > 0 else listed
