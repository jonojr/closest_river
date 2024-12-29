from vectortiles.views import MVTView
from vectortiles.views import TileJSONView

from closest_river.rivers.vector_layers import RiverSectionVectorLayer


class RiverSectionBaseLayer:
    layer_classes = [RiverSectionVectorLayer]


class RiverSectionTileView(RiverSectionBaseLayer, MVTView):
    pass


class RiverSectionTileJSON(RiverSectionBaseLayer, TileJSONView):
    name = "All rivers dataset"
    attribution = "OSM"
    description = "All ways of type river from OSM"

    tile_url = "/rivers/river-sections/tile/{x}/{y}/{z}"
