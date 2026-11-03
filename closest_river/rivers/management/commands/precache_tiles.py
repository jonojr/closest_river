from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from closest_river.rivers.tasks import DEFAULT_WARM_BATCH_SIZE
from closest_river.rivers.tasks import warm_tile_cache


class Command(BaseCommand):
    help = (
        "Queue every tile up to --max-zoom to be rendered into the tile cache, "
        "so no visitor waits on them. For warming the whole cache, such as "
        "after a deploy that changes its keys; the daily sync already re-renders "
        "the tiles it evicts. Runs on the osm queue, lowest zoom first, in "
        "batches that each show in the admin under Task results. The admin's "
        "OSM sync state page has a button that does the same."
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
            default=DEFAULT_WARM_BATCH_SIZE,
            help=f"Tiles per task (default: {DEFAULT_WARM_BATCH_SIZE}).",
        )

    def handle(self, *args, max_zoom, batch_size, **options):
        try:
            tiles, batches = warm_tile_cache(max_zoom, batch_size)
        except ValueError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {tiles:,} tiles (zoom 0-{max_zoom}) "
                f"in {batches:,} tasks on the osm queue.",
            ),
        )
