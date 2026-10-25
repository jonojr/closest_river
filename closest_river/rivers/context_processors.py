"""Template context describing how current the waterway data is.

The map is only ever as fresh as the last pipeline run, so every page says when
that was. Read through a short-lived cache: the underlying value changes once a
day (the sync runs at 03:30) but this query would otherwise run on every single
page render.
"""

import logging

from django.core.cache import cache
from django.db import Error as DatabaseError

from closest_river.rivers.models import SyncState

logger = logging.getLogger(__name__)

_CACHE_KEY = "rivers:data_updated_at"
_CACHE_SECS = 5 * 60

# cache.get() cannot tell "not cached" from "cached as None", and None is a
# legitimate value here — it means nothing has been imported yet.
_MISSING = object()


def data_updated_at():
    """When the waterway data was last refreshed, or None before the first import.

    The later of the two pipeline timestamps. A full rebuild does not touch
    ``last_sync_at``, so straight after bootstrapping that field is still null
    while the data is in fact brand new; taking the max keeps the footer honest
    in both directions.
    """
    cached = cache.get(_CACHE_KEY, _MISSING)
    if cached is not _MISSING:
        return cached

    # Deliberately not SyncState.load(): that get_or_creates, and rendering a
    # template must never write to the database.
    try:
        state = SyncState.objects.filter(pk=SyncState.SINGLETON_PK).first()
    except DatabaseError:
        # base.html backs the 500 page too. A dead database should not turn a
        # useful error page into a second exception.
        logger.exception("Could not read OSM sync state")
        return None

    stamps = []
    if state is not None:
        stamps = [
            stamp
            for stamp in (state.last_sync_at, state.last_full_import_at)
            if stamp is not None
        ]
    updated_at = max(stamps) if stamps else None

    cache.set(_CACHE_KEY, updated_at, timeout=_CACHE_SECS)
    return updated_at


def osm_data_freshness(request):
    """Expose the last data refresh to every template."""
    return {"osm_data_updated_at": data_updated_at()}
