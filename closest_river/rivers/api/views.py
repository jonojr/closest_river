from django.contrib.gis.db.models.functions import GeometryDistance
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

    permission_classes = []

    def get(self, request, lat: str, lon: str):
        user_point = Point(float(lon), float(lat), srid=4326)

        river_section = RiverSection.objects.annotate(
            distance=GeometryDistance("geometry", user_point),
        ).order_by("distance")[:1][0]

        river = river_section.river
        section_serializer = RiverSectionSerializer(river_section)
        river_serializer = RiverSerializer(river_section.river)

        geometry_iterable = river.sections.all() if river else [river_section]

        geometry = serialize("geojson", geometry_iterable)

        trans_geometry = river_section.geometry.transform(3857, clone=True)
        trans_user_point = user_point.transform(3857, clone=True)
        distance = round(trans_geometry.distance(trans_user_point) / 1000, 2)

        return Response(
            {
                "river": river_serializer.data,
                "section": section_serializer.data,
                "distance": distance,
                "geometry": geometry,
            },
        )
