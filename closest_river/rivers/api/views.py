from django.contrib.gis.db.models.functions import ClosestPoint
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.db.models.functions import GeometryDistance
from django.contrib.gis.geos import Point
from django.core.serializers import serialize
from django.http import JsonResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from closest_river.rivers.api.serialziers import RiverSectionSerializer
from closest_river.rivers.api.serialziers import RiverSerializer
from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection


class ClosestRiver(APIView):
    """
    Get the closest river section.
    """

    permission_classes = []

    def get(self, request, lat: str, lon: str):
        user_point = Point(float(lon), float(lat), srid=4326)

        # Do a rough filter using GeometryDistance & grab the 3 closest river sections.
        river_sections = RiverSection.objects.annotate(
            distance=GeometryDistance("geometry", user_point),
        ).order_by("distance")[:3]

        # Calculate the exact distance & closest points to each of the 3 sections.
        # Select the closest.
        river_section = (
            RiverSection.objects.filter(pk__in=river_sections)
            .annotate(
                distance=Distance("geometry", user_point),
                closest_point=ClosestPoint("geometry", user_point),
            )
            .order_by("distance")
            .first()
        )

        river = river_section.river
        section_serializer = RiverSectionSerializer(river_section)
        river_serializer = RiverSerializer(river_section.river)

        geometry_iterable = river.sections.all() if river else [river_section]

        geometry = serialize("geojson", geometry_iterable)

        return Response(
            {
                "river": river_serializer.data,
                "section": section_serializer.data,
                "distance": round(river_section.distance.km, 2),
                "closest_point_on_river": river_section.closest_point.coords,
                "geometry": geometry,
            },
        )


class RiverGeo(APIView):
    permission_classes = []

    def get(self, request, osm_id: int):
        river = River.objects.get(osm_id=osm_id)
        geometry = serialize("geojson", river.sections.all())

        return Response(
            {
                "geometry": geometry,
            },
        )


class RiverElevation(APIView):
    permission_classes = []

    def get(self, request, osm_id: int):
        river = River.objects.get(osm_id=osm_id)

        return JsonResponse(
            river.elevations,
            safe=False,
        )


def process_tag(tag_name: str, value: str | int, all_tags: dict) -> (str, dict):
    if tag_name == "name":
        if "river_osm_id" in all_tags:
            return "Name", {
                "text": value,
                "link": f"/rivers/{all_tags['river_osm_id']}/",
            }
        return "Name", {"text": value}
    if tag_name == "wikipedia":
        return "Wikipedia", {
            "text": value,
            "link": f"https://en.wikipedia.org/wiki/{value}",
        }
    if tag_name == "wikidata":
        return "Wikidata", {
            "text": value,
            "link": f"https://www.wikidata.org/wiki/{value}",
        }

    return tag_name, {"text": value}


class RiverSectionPopupInfo(APIView):
    permission_classes = []

    def get(self, request, osm_way_id: int):
        river_section = RiverSection.objects.get(osm_way_id=osm_way_id)
        river = river_section.river

        popup_data = {}
        if river:
            popup_data.update(river.tags)
            popup_data["river_osm_id"] = river.osm_id

        popup_data.update(river_section.tags)
        popup_data["name"] = river.name if river else river_section.name

        final_popup_data = {}

        for key, value in popup_data.items():
            if key in ["river_osm_id", "osm_id"]:
                continue
            display_key, display_data = process_tag(key, value, popup_data)
            final_popup_data[display_key] = display_data

        return Response(final_popup_data)
