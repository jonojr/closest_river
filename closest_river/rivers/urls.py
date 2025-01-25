from django.urls import path
from django.views.defaults import page_not_found

from closest_river.rivers.api.views import ClosestRiver
from closest_river.rivers.api.views import RiverGeo
from closest_river.rivers.views import RiverDetailView
from closest_river.rivers.views import RiverSectionTileJSON
from closest_river.rivers.views import RiverSectionTileView

app_name = "rivers"
urlpatterns = [
    path(
        "closest_river/<str:lat>/<str:lon>/",
        view=ClosestRiver.as_view(),
        name="closest_river",
    ),
    # serve tiles at /river-sections/<int:z>/<int:x>/<int:y>
    RiverSectionTileView.get_url(),
    path(
        "river-sections/tile/<int:x>/<int:y>/<int:z>",
        RiverSectionTileView.as_view(),
        name="river_section_tiles",
    ),
    path(
        "river-sections/tiles.json",
        RiverSectionTileJSON.as_view(),
        name="river_sections_json",
    ),
    path(
        "features/tile/{z}/{x}/{y}",
        page_not_found,
        name="river_sections_pattern",
    ),
    path(
        "<int:osm_id>/",
        RiverDetailView.as_view(),
        name="river_detail",
    ),
    path(
        "geometry/<int:osm_id>/",
        RiverGeo.as_view(),
        name="river_geo",
    ),
]
