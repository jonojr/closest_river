"""Tests for the unmanaged waterway models.

Almost entirely about RiverMembership's composite primary key, which is the one
place the ORM's view of the osm2pgsql tables is not a plain column-for-column
mapping — and the one place a schema change in waterways.lua could leave the
models still importing cleanly while silently losing rows.
"""

import pytest
from django.db import IntegrityError
from django.db import transaction

from closest_river.rivers.models import RiverMembership
from closest_river.rivers.models import is_intermittent
from closest_river.rivers.models import unnamed_waterway
from closest_river.rivers.tests.factories import RiverFactory
from closest_river.rivers.tests.factories import RiverMembershipFactory
from closest_river.rivers.tests.factories import RiverSectionFactory

pytestmark = pytest.mark.django_db


def test_the_membership_key_is_the_relation_and_position_pair():
    membership = RiverMembershipFactory(sequence=3)

    assert membership.pk == (membership.river_id, 3)
    assert RiverMembership.objects.get(pk=(membership.river_id, 3)) == membership


def test_a_way_can_belong_to_several_relations():
    """The reason this table exists at all: RiverSection keeps only one."""
    shared_way = 424242
    first = RiverMembershipFactory(way_osm_id=shared_way, sequence=1)
    second = RiverMembershipFactory(way_osm_id=shared_way, sequence=1)

    assert first.river_id != second.river_id
    assert RiverMembership.objects.filter(way_osm_id=shared_way).count() == 2


def test_one_position_in_a_relation_cannot_be_used_twice():
    """Guards the unique index the key depends on, in the test DDL as well.

    osm2pgsql assigns sequence per relation, so a duplicate means either the
    flex style or conftest's mirror of it has drifted.
    """
    membership = RiverMembershipFactory(sequence=1)

    with transaction.atomic(), pytest.raises(IntegrityError):
        RiverMembershipFactory(river=membership.river, sequence=1)


def test_memberships_read_back_in_member_order():
    river = RiverFactory()
    for sequence in (3, 1, 2):
        RiverMembershipFactory(river=river, sequence=sequence)

    ordered = river.memberships.order_by("river_id", "sequence")

    assert [membership.sequence for membership in ordered] == [1, 2, 3]


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"intermittent": "yes"}, True),
        ({"seasonal": "wet_season"}, True),
        ({"seasonal": "yes"}, True),
        ({"intermittent": "no"}, False),
        ({"seasonal": "no"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_is_intermittent(tags, expected):
    assert is_intermittent(tags) is expected


def test_a_river_is_intermittent_when_most_sections_are():
    """Relations are often untagged when nearly every member way is."""
    river = RiverFactory(tags={"type": "waterway"})
    for _ in range(2):
        RiverSectionFactory(river=river, tags={"intermittent": "yes"})
    RiverSectionFactory(river=river, tags={})

    assert river.is_mostly_intermittent()


def test_a_river_is_not_intermittent_when_only_some_sections_are():
    river = RiverFactory(tags={"type": "waterway"})
    RiverSectionFactory(river=river, tags={"intermittent": "yes"})
    for _ in range(2):
        RiverSectionFactory(river=river, tags={})

    assert not river.is_mostly_intermittent()


def test_a_tagged_relation_is_intermittent_whatever_its_sections_say():
    river = RiverFactory(tags={"type": "waterway", "intermittent": "yes"})
    RiverSectionFactory(river=river, tags={})

    assert river.is_mostly_intermittent()


@pytest.mark.parametrize(
    ("waterway", "expected"),
    [
        ("stream", "Unnamed stream"),
        ("tidal_channel", "Unnamed tidal channel"),
        (None, "Unnamed waterway"),
    ],
)
def test_unnamed_waterway(waterway, expected):
    assert unnamed_waterway(waterway) == expected


def test_a_section_is_named_by_its_own_tag_not_its_drawn_class():
    """A drain promoted into a river is drawn as a river, but is a drain."""
    section = RiverSectionFactory(
        name=None,
        waterway="river",
        tags={"waterway": "drain"},
    )

    assert section.display_name == "Unnamed drain"


def test_a_named_section_keeps_its_name():
    assert RiverSectionFactory(name="Merri Creek").display_name == "Merri Creek"


def test_a_river_with_no_name_or_type_is_a_waterway():
    river = RiverFactory(name=None, tags={"type": "waterway"})

    assert river.display_name == "Unnamed waterway"
