from django.contrib import admin
from django.contrib import messages

from closest_river.rivers.models import OsmData
from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection
from closest_river.rivers.tasks import ingest_osm_data


@admin.register(RiverSection)
class RiverSectionAdmin(admin.ModelAdmin):
    pass


@admin.register(River)
class RiverAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(OsmData)
class OsmDataAdmin(admin.ModelAdmin):
    actions = ["run_ingestion"]

    @admin.action(
        description="Run ingestion for file",
    )
    def run_ingestion(self, request, queryset):
        for osm_ingestion in queryset:
            ingest_osm_data.delay(osm_ingestion.pk)

        messages.success(request, "Running OSM Ingestion")
