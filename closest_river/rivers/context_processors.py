"""Template context describing how current the waterway data is.

The map is only ever as fresh as the OSM edits it has applied, so every page
says when the newest of those was. Read through a short-lived cache: the
underlying value changes once a day (the sync runs at 03:30) but these queries
would otherwise run on every single page render.
"""

import logging

from django.core.cache import cache
from django.db import Error as DatabaseError

from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import SyncState

logger = logging.getLogger(__name__)

_CACHE_KEY = "rivers:data_updated_at"
_CACHE_SECS = 5 * 60

# cache.get() cannot tell "not cached" from "cached as None", and None is a
# legitimate value here — it means nothing has been imported yet.
_MISSING = object()


def data_updated_at():
    """How current the waterway data is, or None before the first import.

    The time of the newest OSM edit applied, from osm2pgsql's replication
    cursor, rather than when the pipeline last ran: a sync that applies
    nothing, because the feed has stalled, must not make the data look fresh.
    """
    cached = cache.get(_CACHE_KEY, _MISSING)
    if cached is not _MISSING:
        return cached

    try:
        updated_at = osm_pipeline.replication_timestamp() or _last_pipeline_run()
    except DatabaseError:
        # base.html backs the 500 page too. A dead database should not turn a
        # useful error page into a second exception.
        logger.exception("Could not read how current the OSM data is")
        return None

    cache.set(_CACHE_KEY, updated_at, timeout=_CACHE_SECS)
    return updated_at


def _last_pipeline_run():
    """The later of the two pipeline timestamps, for a database whose
    replication has not been initialised (a hand-seeded development one).

    A full rebuild does not touch ``last_sync_at``, so straight after
    bootstrapping that field is still null while the data is in fact brand new;
    taking the max keeps the footer honest in both directions.
    """
    # Deliberately not SyncState.load(): that get_or_creates, and rendering a
    # template must never write to the database.
    state = SyncState.objects.filter(pk=SyncState.SINGLETON_PK).first()
    if state is None:
        return None
    stamps = [
        stamp
        for stamp in (state.last_sync_at, state.last_full_import_at)
        if stamp is not None
    ]
    return max(stamps) if stamps else None


def osm_data_freshness(request):
    """Expose how current the data is to every template."""
    return {"osm_data_updated_at": data_updated_at()}
