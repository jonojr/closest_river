from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.core.serializers import serialize
from rest_framework.response import Response
from rest_framework.views import APIView

from closest_river.rivers.api.serialziers import RiverSectionSerializer
from closest_river.rivers.api.serialziers import RiverSerializer
from closest_river.rivers.models import RiverSection


class ClosestRiver(APIView):
    """
    Get the closest river section.
    """

    def get(self, request, lat: str, lon: str):
        user_point = Point(float(lon), float(lat), srid=4326)

        river_section = (
            RiverSection.objects.annotate(
                distance=Distance("geometry", user_point),
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
                "geometry": geometry,
            },
        )
