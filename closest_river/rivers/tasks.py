"""Celery tasks for loading and syncing OSM waterway data.

Both tasks are routed to the ``osm`` queue (see CELERY_TASK_ROUTES), which is
served only by the osmworker container — the only image with osm2pgsql and
osmium installed, and the only one with the data volume mounted.
"""

import logging
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import SyncState
from closest_river.rivers.tile_cache import bump_data_version
from closest_river.rivers.tile_cache import invalidate_expired_tiles
from config.celery_app import app

logger = logging.getLogger(__name__)

_LOCK_KEY = "rivers:osm-pipeline-lock"

# Long enough that a slow run does not lose its own lock, short enough that a
# hard-killed worker does not block the pipeline indefinitely.
_SYNC_LOCK_TIMEOUT = 12 * 60 * 60
_IMPORT_LOCK_TIMEOUT = 3 * 24 * 60 * 60


class PipelineBusyError(Exception):
    """Another import or sync already holds the lock."""


@contextmanager
def _pipeline_lock(owner: str, timeout: int):
    """Guard against two pipeline runs touching the database at once.

    The osm queue worker runs at concurrency 1, so this mainly protects against
    a manually triggered run overlapping the scheduled one. cache.add is atomic
    on Redis.
    """
    if not cache.add(_LOCK_KEY, owner, timeout=timeout):
        holder = cache.get(_LOCK_KEY)
        msg = f"OSM pipeline is already running ({holder}); skipping {owner}"
        raise PipelineBusyError(msg)
    try:
        yield
    finally:
        cache.delete(_LOCK_KEY)


@app.task(
    name="closest_river.rivers.tasks.sync_osm_waterways",
    # Far above a normal day's diff, but bounded so a wedged run is eventually
    # killed rather than holding the queue forever.
    time_limit=6 * 60 * 60,
    soft_time_limit=5 * 60 * 60 + 45 * 60,
)
def sync_osm_waterways() -> dict:
    """Apply OSM replication diffs and invalidate the tiles they changed.

    Runs daily. osm2pgsql keeps the replication cursor in its properties table
    and writes it in the same transaction as the data, so a failed run simply
    reapplies the same diffs next time rather than skipping them.
    """
    # Imported here to avoid pulling the tile layers (and their model imports)
    # into every worker at module load.
    from closest_river.rivers.views import RiverSectionBaseLayer

    layer_ids = [layer.id for layer in RiverSectionBaseLayer.layer_classes]

    with _pipeline_lock("sync_osm_waterways", _SYNC_LOCK_TIMEOUT):
        osm_pipeline.replication_update()
        osm_pipeline.analyze_schema(settings.OSM_SCHEMA)

        tiles_expired = invalidate_expired_tiles(layer_ids)

        state = SyncState.load()
        state.last_sync_at = timezone.now()
        state.last_sync_tiles_expired = tiles_expired
        state.save(update_fields=["last_sync_at", "last_sync_tiles_expired"])

    logger.info("OSM sync complete; %s cached tiles invalidated", tiles_expired)
    return {"tiles_expired": tiles_expired}


@app.task(
    name="closest_river.rivers.tasks.full_import_osm_waterways",
    # A planet import is measured in hours. Deliberately generous.
    time_limit=48 * 60 * 60,
    soft_time_limit=47 * 60 * 60,
)
def full_import_osm_waterways(*, download: bool = True) -> dict:
    """Rebuild the whole dataset from a fresh planet file.

    Run once to bootstrap, then periodically (monthly is a reasonable starting
    point) to reset accumulated drift: a way retagged *into* a waterway whose
    own nodes were not edited in the same diff is missed by incremental
    updates, because those node locations were never in the filtered middle.

    Builds into a staging schema and swaps atomically, so the live schema keeps
    serving traffic until the new data is complete.
    """
    live_schema = settings.OSM_SCHEMA
    staging_schema = f"{live_schema}_staging"

    with _pipeline_lock("full_import_osm_waterways", _IMPORT_LOCK_TIMEOUT):
        planet = osm_pipeline.data_dir() / "planet-latest.osm.pbf"
        if download:
            planet = osm_pipeline.download_planet(destination=planet)
        elif not planet.exists():
            msg = f"{planet} does not exist and download was disabled"
            raise osm_pipeline.OsmPipelineError(msg)

        waterways = osm_pipeline.filter_waterways(planet)

        osm_pipeline.import_pbf(waterways, schema=staging_schema)
        osm_pipeline.swap_schema(staging=staging_schema, live=live_schema)

        # Init after the swap so the cursor is recorded against the live schema.
        osm_pipeline.replication_init(waterways, schema=live_schema)
        osm_pipeline.analyze_schema(live_schema)

        # A rebuild emits no expiry information — osm2pgsql only expires in
        # append mode — and invalidates essentially everything anyway, so this
        # is the one case where stranding the entire tile cache is correct.
        version = bump_data_version()

        state = SyncState.load()
        state.last_full_import_at = timezone.now()
        state.save(update_fields=["last_full_import_at"])

    logger.info("Full OSM import complete; tile data version now %s", version)
    return {"data_version": version}
