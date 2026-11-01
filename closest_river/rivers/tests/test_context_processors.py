"""The data-freshness line in the footer.

What matters: the date shown is how current the OSM data is (the newest edit
applied), not merely when the pipeline last ran; nothing is claimed before the
first import; and rendering a page never writes to the database.
"""

from datetime import UTC
from datetime import datetime

import pytest
from django.db import Error as DatabaseError

from closest_river.rivers import osm_pipeline
from closest_river.rivers.context_processors import data_updated_at
from closest_river.rivers.context_processors import osm_data_freshness
from closest_river.rivers.models import SyncState

pytestmark = pytest.mark.django_db

SYNC = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)
REPLICATED = datetime(2026, 7, 19, 20, 20, 51, tzinfo=UTC)
IMPORT = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)


def _set_state(**fields):
    SyncState.objects.update_or_create(pk=SyncState.SINGLETON_PK, defaults=fields)


def test_returns_none_before_anything_has_been_imported():
    assert data_updated_at() is None


def test_reports_the_newest_osm_edit_applied(replication_properties):
    _set_state(last_sync_at=SYNC, last_full_import_at=IMPORT)
    replication_properties(replication_timestamp="2026-07-19T20:20:51Z")

    assert data_updated_at() == REPLICATED


def test_a_stalled_feed_does_not_look_fresh(replication_properties):
    """The sync keeps running, but if no new diffs arrive the data ages."""
    replication_properties(replication_timestamp="2026-07-01T00:00:00Z")
    _set_state(last_sync_at=SYNC)

    assert data_updated_at() == datetime(2026, 7, 1, tzinfo=UTC)


def test_reports_the_incremental_sync_when_it_is_the_most_recent():
    _set_state(last_sync_at=SYNC, last_full_import_at=IMPORT)

    assert data_updated_at() == SYNC


def test_falls_back_to_the_full_import_when_no_sync_has_run_yet():
    """A fresh bootstrap leaves last_sync_at null, but the data is brand new."""
    _set_state(last_sync_at=None, last_full_import_at=IMPORT)

    assert data_updated_at() == IMPORT


def test_prefers_the_full_import_when_it_is_newer_than_the_last_sync():
    """A rebuild after a period of syncing is still the freshest data."""
    newer_import = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
    _set_state(last_sync_at=SYNC, last_full_import_at=newer_import)

    assert data_updated_at() == newer_import


def test_does_not_create_the_singleton_row():
    """A template render must not write. load() would get_or_create."""
    data_updated_at()

    assert not SyncState.objects.exists()


def test_caches_the_lookup():
    _set_state(last_sync_at=SYNC)
    assert data_updated_at() == SYNC

    _set_state(last_sync_at=datetime(2026, 7, 26, 3, 30, tzinfo=UTC))

    assert data_updated_at() == SYNC, "should be served from cache"


def test_caches_the_absence_of_data():
    """None is a real value, not a cache miss to be retried every render."""
    assert data_updated_at() is None

    _set_state(last_sync_at=SYNC)

    assert data_updated_at() is None, "should be served from cache"


def test_context_processor_exposes_the_timestamp():
    _set_state(last_sync_at=SYNC)

    assert osm_data_freshness(request=None) == {"osm_data_updated_at": SYNC}


def test_survives_the_database_being_unavailable(monkeypatch):
    """base.html backs the 500 page; a dead database must not break it."""

    def explode(*args, **kwargs):
        msg = "connection refused"
        raise DatabaseError(msg)

    monkeypatch.setattr(osm_pipeline, "replication_properties", explode)

    assert data_updated_at() is None


def test_the_footer_shows_the_date_on_a_real_page(client, replication_properties):
    """End to end: the processor is wired up and base.html renders the date."""
    _set_state(last_sync_at=SYNC)
    replication_properties(replication_timestamp="2026-07-19T20:20:51Z")

    html = client.get("/").content.decode()

    assert "River data from" in html
    # Rendered in TIME_ZONE (Australia/Melbourne): 20:20 UTC is 6:20am the next day.
    assert "as of" in html
    assert "20 Jul 2026, 6:20 a.m." in html
    assert 'datetime="2026-07-20T06:20:51+10:00"' in html


def test_the_footer_claims_no_date_before_the_first_import(client):
    html = client.get("/").content.decode()

    assert "River data from" in html
    assert "as of" not in html
