"""Celery tasks for loading and syncing OSM waterway data.

Both tasks are routed to the ``osm`` queue (see CELERY_TASK_ROUTES), which is
served only by the osmworker container — the only image with osm2pgsql and
osmium installed, and the only one with the data volume mounted.
"""

import logging
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from closest_river.rivers import basemap
from closest_river.rivers import osm_pipeline
from closest_river.rivers.models import SyncState
from closest_river.rivers.tile_cache import bump_data_version
from closest_river.rivers.tile_cache import high_effort_tiles
from closest_river.rivers.tile_cache import invalidate_expired_tiles
from closest_river.rivers.tile_cache import precache_tiles
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


class _Steps:
    """Logs each step of a task as it starts and how long it took.

    A full import runs for hours, mostly in single steps, so the log should
    say which step it is on and how far through the task that is.
    """

    def __init__(self, task: str, total: int):
        self.task = task
        self.total = total
        self.number = 0
        self.started = time.monotonic()

    @contextmanager
    def step(self, description: str):
        self.number += 1
        label = f"{self.task} [{self.number}/{self.total}] {description}"
        logger.info("%s", label)
        started = time.monotonic()
        yield
        elapsed = osm_pipeline.format_duration(time.monotonic() - started)
        logger.info("%s: done in %s", label, elapsed)

    @property
    def elapsed(self) -> str:
        return osm_pipeline.format_duration(time.monotonic() - self.started)


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

    Afterwards it queues the costliest of the evicted tiles to be rendered
    again before anyone asks for them (see precache_high_effort_tiles).
    """
    steps = _Steps("OSM sync", total=4)

    with _pipeline_lock("sync_osm_waterways", _SYNC_LOCK_TIMEOUT):
        with steps.step("Applying replication diffs"):
            osm_pipeline.replication_update()
        # Before the invalidation below, which evicts the tiles this restyles.
        with steps.step("Restyling rivers by their overall intermittency"):
            osm_pipeline.apply_river_intermittency(settings.OSM_SCHEMA, expire=True)
        with steps.step("Analyzing tables"):
            osm_pipeline.analyze_schema(settings.OSM_SCHEMA)
        with steps.step("Invalidating changed tiles"):
            expired = invalidate_expired_tiles()
        tiles_expired = len(expired)

        state = SyncState.load()
        state.last_sync_at = timezone.now()
        state.last_sync_tiles_expired = tiles_expired
        state.save(update_fields=["last_sync_at", "last_sync_tiles_expired"])

    logger.info(
        "OSM sync complete in %s; %s cached tiles invalidated",
        steps.elapsed,
        tiles_expired,
    )

    # A task of its own, after the lock is released: it only reads, and a
    # failure here should not mark the sync itself as failed.
    to_precache = high_effort_tiles(expired, settings.TILE_PRECACHE_MAX_ZOOM)
    if to_precache:
        precache_high_effort_tiles.delay(to_precache)

    return {"tiles_expired": tiles_expired, "tiles_to_precache": len(to_precache)}


@app.task(
    name="closest_river.rivers.tasks.precache_high_effort_tiles",
    # Shares the osm queue's single slot, so bounded well short of the next
    # day's sync. The tiles arrive lowest zoom first, so a run cut short loses
    # only the cheapest of them.
    time_limit=3 * 60 * 60,
    soft_time_limit=2 * 60 * 60 + 45 * 60,
)
def precache_high_effort_tiles(
    tiles: list[list[int]],
    batch: str | None = None,
) -> dict:
    """Render the low-zoom tiles a sync evicted back into the cache.

    These merge the most of the network and can take a second each, so
    otherwise the first visitor after every sync pays for each one.

    batch, such as "3 of 175", says where this task falls in a precache_tiles
    run, so the log shows how far through the whole run it is.
    """
    # Imported here to avoid pulling the tile layers (and their model imports)
    # into every worker at module load.
    from closest_river.rivers.views import RiverSectionTileView

    layers = RiverSectionTileView().get_layers()
    started = time.monotonic()
    label = f"{len(tiles)} tiles"
    if tiles:
        lowest, highest = min(t[0] for t in tiles), max(t[0] for t in tiles)
        zooms = str(lowest) if lowest == highest else f"{lowest}-{highest}"
        label += f" (zoom {zooms})"
    if batch:
        label += f", batch {batch}"
    logger.info("Precaching %s", label)

    rendered = precache_tiles(layers, [tuple(tile) for tile in tiles])

    logger.info(
        "Precached %s in %s",
        label,
        osm_pipeline.format_duration(time.monotonic() - started),
    )
    result = {"tiles_precached": rendered}
    if batch:
        result["batch"] = batch
    return result


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

    steps = _Steps("Full OSM import", total=8 if download else 7)

    with _pipeline_lock("full_import_osm_waterways", _IMPORT_LOCK_TIMEOUT):
        planet = osm_pipeline.data_dir() / "planet-latest.osm.pbf"
        if download:
            with steps.step("Downloading the planet"):
                planet = osm_pipeline.download_planet(destination=planet)
        elif not planet.exists():
            msg = f"{planet} does not exist and download was disabled"
            raise osm_pipeline.OsmPipelineError(msg)

        with steps.step("Filtering to waterways"):
            waterways = osm_pipeline.filter_waterways(planet)

        with steps.step(f"Importing into {staging_schema}"):
            osm_pipeline.import_pbf(waterways, schema=staging_schema)
        # Before the swap, so the live schema is never drawn way by way.
        with steps.step("Restyling rivers by their overall intermittency"):
            osm_pipeline.apply_river_intermittency(staging_schema, expire=False)
        with steps.step(f"Swapping {staging_schema} in as {live_schema}"):
            osm_pipeline.swap_schema(staging=staging_schema, live=live_schema)

        # Init after the swap so the cursor is recorded against the live schema.
        with steps.step("Initialising replication"):
            osm_pipeline.replication_init(waterways, schema=live_schema)
        with steps.step("Analyzing tables"):
            osm_pipeline.analyze_schema(live_schema)

        # A rebuild emits no expiry information — osm2pgsql only expires in
        # append mode — and invalidates essentially everything anyway, so this
        # is the one case where stranding the entire tile cache is correct.
        with steps.step("Invalidating the tile cache"):
            version = bump_data_version()

        state = SyncState.load()
        state.last_full_import_at = timezone.now()
        state.save(update_fields=["last_full_import_at"])

    logger.info(
        "Full OSM import complete in %s; tile data version now %s",
        steps.elapsed,
        version,
    )
    return {"data_version": version}


@app.task(
    name="closest_river.rivers.tasks.fetch_basemap",
    # A planet extract is a few hours of range requests; a regional one,
    # minutes.
    time_limit=24 * 60 * 60,
    soft_time_limit=23 * 60 * 60,
)
def fetch_basemap(*, terrain: bool = True, assets: bool = True) -> None:
    """Download the self-hosted basemap into BASEMAP_DIR (see rivers/basemap.py).

    Touches no database tables, so it does not take the pipeline lock; the osm
    queue's concurrency of 1 already keeps it from running beside an import.
    """
    basemap.fetch_all(terrain=terrain, assets=assets)
