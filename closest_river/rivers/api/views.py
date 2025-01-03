from django.contrib.gis.db.models.functions import ClosestPoint
from django.contrib.gis.db.models.functions import Distance
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
