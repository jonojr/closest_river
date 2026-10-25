"""Factories for the waterway models.

The models are unmanaged, but ``managed`` only affects migrations — Django will
happily INSERT into the tables, so ordinary factories work. Primary keys are
OSM ids rather than sequences, so they must always be supplied.
"""

import factory
from django.contrib.gis.geos import LineString
from factory.django import DjangoModelFactory

from closest_river.rivers.models import River
from closest_river.rivers.models import RiverMembership
from closest_river.rivers.models import RiverSection


class RiverFactory(DjangoModelFactory):
    class Meta:
        model = River

    osm_id = factory.Sequence(lambda n: 900000 + n)
    name = factory.Sequence(lambda n: f"River {n}")
    destination = ""
    wikipedia = ""
    tags = factory.LazyAttribute(lambda o: {"type": "waterway", "name": o.name})


class RiverSectionFactory(DjangoModelFactory):
    class Meta:
        model = RiverSection

    osm_way_id = factory.Sequence(lambda n: 800000 + n)
    name = factory.Sequence(lambda n: f"Section {n}")
    waterway = "river"
    role = None
    node_ids = factory.LazyFunction(lambda: [1, 2])
    tags = factory.LazyAttribute(lambda o: {"waterway": o.waterway, "name": o.name})
    river = None

    @factory.lazy_attribute
    def geometry(self):
        # A short line near Melbourne, offset per instance so sections do not
        # all sit on top of each other.
        offset = (self.osm_way_id % 100) * 0.01
        return LineString(
            (144.9 + offset, -37.8),
            (144.95 + offset, -37.85),
            srid=4326,
        )


class RiverMembershipFactory(DjangoModelFactory):
    class Meta:
        model = RiverMembership

    river = factory.SubFactory(RiverFactory)
    way_osm_id = factory.Sequence(lambda n: 800000 + n)
    role = "main_stream"
    sequence = factory.Sequence(lambda n: n + 1)
