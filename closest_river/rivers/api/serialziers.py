from rest_framework.serializers import ModelSerializer

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection


class RiverSectionSerializer(ModelSerializer):
    class Meta:
        model = RiverSection
        fields = "__all__"


class RiverSerializer(ModelSerializer):
    class Meta:
        model = River
        fields = "__all__"
