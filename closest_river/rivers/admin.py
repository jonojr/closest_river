from itertools import groupby

from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.utils.html import format_html_join

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection
from closest_river.rivers.models import SyncState
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL

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
        """Every cached-zoom tile the line crosses, as (x, y) per zoom.

        These are the cache entries an edit to this way evicts. Worked out
        against the line itself rather than its bounding box, so a diagonal
        river does not list tiles it never enters.
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
    """

    list_display = (
        "last_sync_at",
        "cache_keys_evicted",
        "last_full_import_at",
        "data_version",
    )
    fields = list_display

    @admin.display(description="Cache keys evicted by the last sync")
    def cache_keys_evicted(self, obj):
        # One key per tile per layer, which the field name does not say.
        return obj.last_sync_tiles_expired


def _tiles_crossed(osm_way_id: int) -> list[tuple[int, int, int]]:
    """(z, x, y) of every cached-zoom tile a waterway's line intersects.

    The bounding box narrows each zoom to a range of candidate tiles first, so
    only those are tested against the line.
    """
    table = f'"{settings.OSM_SCHEMA}"."waterway_tile"'
    # Half the Web Mercator world's width, in metres.
    half_world = 20037508.342789244
    tile_index = "LEAST(GREATEST(floor({} * 2 ^ z)::int, 0), (2 ^ z)::int - 1)"
    west = tile_index.format(f"(ST_XMin(g) + {half_world}) / {2 * half_world}")
    east = tile_index.format(f"(ST_XMax(g) + {half_world}) / {2 * half_world}")
    north = tile_index.format(f"({half_world} - ST_YMax(g)) / {2 * half_world}")
    south = tile_index.format(f"({half_world} - ST_YMin(g)) / {2 * half_world}")
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH way AS (SELECT geometry AS g FROM {table} WHERE osm_way_id = %s),
            ranges AS (
                SELECT z, {west} AS x0, {east} AS x1, {north} AS y0, {south} AS y1
                FROM way, generate_series(0, %s) AS z
            )
            SELECT z, x, y
            FROM ranges, way,
                 generate_series(x0, x1) AS x,
                 generate_series(y0, y1) AS y
            WHERE ST_Intersects(g, ST_TileEnvelope(z, x, y))
            ORDER BY z, x, y
            """,  # noqa: S608
            [osm_way_id, CACHE_BELOW_ZOOM_LEVEL - 1],
        )
        return cursor.fetchall()


def _describe_tiles(tiles: list[tuple[int, int]]) -> str:
    listed = ", ".join(f"({x}, {y})" for x, y in tiles[:_MAX_TILES_LISTED])
    hidden = len(tiles) - _MAX_TILES_LISTED
    return f"{listed} and {hidden} more" if hidden > 0 else listed
