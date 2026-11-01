from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from closest_river.rivers.tasks import precache_high_effort_tiles
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL

# Each batch is one precache_high_effort_tiles task, which is killed after three
# hours. At up to a second a tile, 500 stays well inside that.
DEFAULT_BATCH_SIZE = 500


def every_tile(max_zoom: int) -> list[list[int]]:
    """Every z/x/y tile from zoom 0 to max_zoom, lowest zoom first."""
    return [
        [z, x, y]
        for z in range(max_zoom + 1)
        for x in range(1 << z)
        for y in range(1 << z)
    ]


class Command(BaseCommand):
    help = (
        "Queue every tile up to --max-zoom to be rendered into the tile cache, "
        "so no visitor waits on them. For warming the whole cache, such as "
        "after a deploy that changes its keys; the daily sync already re-renders "
        "the tiles it evicts. Runs on the osm queue, lowest zoom first, in "
        "batches that each show in the admin under Task results."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-zoom",
            type=int,
            default=settings.TILE_PRECACHE_MAX_ZOOM,
            help=(
                "Highest zoom to render (default: TILE_PRECACHE_MAX_ZOOM, "
                f"currently {settings.TILE_PRECACHE_MAX_ZOOM}). Zoom n adds 4^n "
                "tiles: 0-6 is 5,461 tiles, 0-8 is 87,381."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Tiles per task (default: {DEFAULT_BATCH_SIZE}).",
        )

    def handle(self, *args, max_zoom, batch_size, **options):
        highest_cached = CACHE_BELOW_ZOOM_LEVEL - 1
        if not 0 <= max_zoom <= highest_cached:
            msg = (
                f"--max-zoom must be 0 to {highest_cached}: tiles above that are "
                "never cached, so rendering them ahead would be wasted."
            )
            raise CommandError(msg)
        if batch_size < 1:
            msg = "--batch-size must be at least 1."
            raise CommandError(msg)

        tiles = every_tile(max_zoom)
        starts = range(0, len(tiles), batch_size)
        batches = len(starts)
        for number, start in enumerate(starts, start=1):
            precache_high_effort_tiles.delay(
                tiles[start : start + batch_size],
                batch=f"{number} of {batches}",
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {len(tiles):,} tiles (zoom 0-{max_zoom}) "
                f"in {batches:,} tasks on the osm queue.",
            ),
        )
