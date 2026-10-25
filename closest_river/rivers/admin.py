from django.contrib import admin

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection


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


# RiverMembership is deliberately not registered. It is keyed on a composite
# primary key, which the admin does not support: every change URL it builds is
# a single quoted pk. The denormalised most-significant role is on RiverSection
# above, which covers what the admin was ever used to look up; the full
# membership rows are a query away in the shell.
