from django.contrib import admin

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection
from closest_river.rivers.models import SyncState
from closest_river.rivers.models import WaterwayTile


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
