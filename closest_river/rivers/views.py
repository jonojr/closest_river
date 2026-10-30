from django.views.generic import DetailView
from vectortiles.views import MVTView
from vectortiles.views import TileJSONView

from closest_river.rivers.models import River
from closest_river.rivers.vector_layers import RiverSectionVectorLayer
from closest_river.rivers.vector_layers import StreamSectionVectorLayer


class RiverSectionBaseLayer:
    # Streams first so rivers are drawn over the wash by default.
    layer_classes = [StreamSectionVectorLayer, RiverSectionVectorLayer]


class RiverSectionTileView(RiverSectionBaseLayer, MVTView):
    pass


class RiverSectionTileJSON(RiverSectionBaseLayer, TileJSONView):
    name = "All rivers dataset"
    attribution = "OSM"
    description = "All ways of type river from OSM"

    tile_url = "/rivers/river-sections/tile/{x}/{y}/{z}"


class RiverDetailView(DetailView):
    model = River
    template_name = "pages/river_detail.html"
    context_object_name = "river"
    slug_field = "osm_id"
    slug_url_kwarg = "osm_id"
