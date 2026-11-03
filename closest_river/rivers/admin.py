from datetime import timedelta
from itertools import groupby

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.shortcuts import redirect
from django.template.defaultfilters import filesizeformat
from django.template.defaultfilters import pluralize
from django.template.response import TemplateResponse
from django.urls import path
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.timesince import timesince
from django.utils.timezone import localtime
from django.utils.timezone import now
from redis.exceptions import RedisError

from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection
from closest_river.rivers.models import SyncState
from closest_river.rivers.models import WaterwayTile
from closest_river.rivers.tasks import DEFAULT_WARM_BATCH_SIZE
from closest_river.rivers.tasks import warm_tile_cache
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL
from closest_river.rivers.tile_cache import cache_coverage
from closest_river.rivers.tile_cache import cache_stats
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
    fields = (*list_display, "replication", "osm_schemas", "tile_cache")

    def get_urls(self):
        warm = path(
            "warm-tile-cache/",
            self.admin_site.admin_view(self.warm_tile_cache_view),
            name="rivers_syncstate_warm_tile_cache",
        )
        return [warm, *super().get_urls()]

    def has_warm_permission(self, request):
        """Superusers only: a full warm holds the osm queue's single slot (about
        30 minutes for zooms 0-8 on the planet), and the sync waits behind it.
        """
        return request.user.is_superuser

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "can_warm_tile_cache": self.has_warm_permission(request),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def warm_tile_cache_view(self, request):
        """Confirm, then queue every tile up to a zoom, as precache_tiles does."""
        if not self.has_warm_permission(request):
            raise PermissionDenied
        form = WarmTileCacheForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            max_zoom = form.cleaned_data["max_zoom"]
            tiles, batches = warm_tile_cache(max_zoom)
            messages.success(
                request,
                f"Queued {tiles:,} tiles (zoom 0-{max_zoom}) in "
                f"{batches:,} task{pluralize(batches)} on the osm queue. Each "
                "shows under Task results, and the Tile cache section below "
                "fills in as they run.",
            )
            return redirect(
                reverse("admin:rivers_syncstate_change", args=[SyncState.load().pk]),
            )
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": "Warm the tile cache",
            "form": form,
            "sync_state": SyncState.load(),
        }
        return TemplateResponse(
            request,
            "admin/rivers/syncstate/warm_tile_cache.html",
            context,
        )

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

    @admin.display(description="Tile cache")
    def tile_cache(self, obj):
        """How much of each precached zoom is in the cache, and how much memory
        it takes. Stops at TILE_PRECACHE_MAX_ZOOM: checking means looking up
        every tile's key, and zoom 9-10 alone is 1.3 million of them.
        """
        try:
            stats = cache_stats()
            coverage = cache_coverage(settings.TILE_PRECACHE_MAX_ZOOM)
        except RedisError as error:
            return f"Could not read the cache: {error}"
        summary = ""
        if stats:
            limit = filesizeformat(stats["max"]) if stats["max"] else "no limit"
            started = now() - timedelta(seconds=stats["uptime"])
            summary = format_html(
                "<p>{} used of {}, evicting by {}.</p>"
                "<p>{} keys evicted since Valkey started {} ago.</p>",
                filesizeformat(stats["used"]),
                limit,
                stats["policy"],
                f"{stats['evicted']:,}",
                timesince(started),
            )
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td>{} of {}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    zoom.zoom,
                    f"{zoom.cached:,}",
                    f"{zoom.total:,}",
                    _percent(zoom.percent),
                    "" if zoom.memory is None else filesizeformat(zoom.memory),
                )
                for zoom in coverage
            ),
        )
        return format_html(
            "{}<table><thead><tr><th>Zoom</th><th>Cached</th><th>%</th>"
            "<th>Memory</th></tr></thead><tbody>{}</tbody></table>",
            summary,
            rows,
        )


def _zoom_choice(max_zoom: int) -> tuple[int, str]:
    tiles = (4 ** (max_zoom + 1) - 1) // 3
    tasks = -(-tiles // DEFAULT_WARM_BATCH_SIZE)
    return (
        max_zoom,
        f"0-{max_zoom}: {tiles:,} tiles in {tasks:,} task{pluralize(tasks)}",
    )


class WarmTileCacheForm(forms.Form):
    max_zoom = forms.TypedChoiceField(
        label="Zooms",
        coerce=int,
        choices=[_zoom_choice(z) for z in range(CACHE_BELOW_ZOOM_LEVEL)],
        help_text=(
            "Each zoom has four times as many tiles as the one before it. "
            "Defaults to TILE_PRECACHE_MAX_ZOOM: the zooms the sync re-renders, "
            "and the ones the Tile cache section shows."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 0 <= settings.TILE_PRECACHE_MAX_ZOOM < CACHE_BELOW_ZOOM_LEVEL:
            self.fields["max_zoom"].initial = settings.TILE_PRECACHE_MAX_ZOOM


def _percent(value: float) -> str:
    """Whole percent, except that a sliver is not rounded away: 1 tile of
    65,536 is not 0%, and 65,535 of them is not 100%.
    """
    if 0 < value < 1:
        return "<1%"
    if 99 < value < 100:  # noqa: PLR2004
        return ">99%"
    return f"{value:.0f}%"


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
