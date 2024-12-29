from vectortiles import VectorLayer

from closest_river.rivers.models import RiverSection


class RiverSectionVectorLayer(VectorLayer):
    model = RiverSection
    geom_field = "geometry"
    id = "river-sections"
    tile_fields = ("name", "tags", "river__tags")
    min_zoom = 0
    # all attributes available in vector layer definition can be defined
