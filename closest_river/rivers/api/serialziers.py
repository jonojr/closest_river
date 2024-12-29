from rest_framework.serializers import ModelSerializer
from rest_framework.serializers import SerializerMethodField

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverSection


class RiverSectionSerializer(ModelSerializer):
    distance = SerializerMethodField()

    def get_distance(self, obj):
        return round(obj.distance.km, 2)

    class Meta:
        model = RiverSection
        fields = "__all__"


class RiverSerializer(ModelSerializer):
    class Meta:
        model = River
        fields = "__all__"
