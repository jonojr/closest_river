import logging

import osmium
from django.apps import apps
from django.contrib.gis.geos import LineString

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection

logger = logging.getLogger(__name__)


class OsmParser(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.model = apps.get_model("rivers", "RiverSection")
        self.way_count = 0

        self.waterway_tags = [
            "river",
            "stream",
        ]

    def way(self, way):
        """This method gets called for every way in OSM File"""
        if "waterway" in way.tags and way.tags["waterway"] in self.waterway_tags:
            self.way_count += 1

            if self.way_count % 100 == 0:
                logger.info(
                    "Way being ingested: %s\nIngested: %s ways",
                    way.id,
                    self.way_count,
                )

            coordinates = [
                (node.location.lon, node.location.lat)
                for node in way.nodes
                if node.location.valid()
            ]

            line_string = None
            if len(coordinates) > 1:
                line_string = LineString(coordinates)

            river_section, created = self.model.objects.get_or_create(
                osm_way_id=way.id,
            )

            has_no_geometry = river_section.geometry is None

            if (
                created
                or has_no_geometry
                or not river_section.geometry.equals(line_string)
            ):
                river_section.tags = dict(way.tags)
                river_section.osm_node_ids = [node.ref for node in way.nodes]
                river_section.geometry = line_string
                river_section.name = way.tags.get("name", "N/A")

                river_section.save(
                    update_fields=(
                        "tags",
                        "name",
                        "osm_node_ids",
                        "geometry",
                    ),
                )

    def relation(self, relation):
        if "type" in relation.tags and relation.tags["type"] == "waterway":
            logger.info("Relation being ingested %s", relation.id)
            river, _ = River.objects.get_or_create(
                osm_id=relation.id,
            )
            river.name = relation.tags.get("name", "Unknown")
            river.destination = relation.tags.get("destination", "")
            river.wikipedia = relation.tags.get("wikipedia", "")
            river.tags = dict(relation.tags)
            river.save()

            way_ids = [
                member.ref for member in relation.members if member.type in ("way", "w")
            ]
            logger.info("Relation has following members: %s", way_ids)
            RiverSection.objects.filter(osm_way_id__in=way_ids).update(river=river)
