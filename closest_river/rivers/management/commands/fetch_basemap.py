from django.core.management.base import BaseCommand

from closest_river.rivers import basemap


class Command(BaseCommand):
    help = (
        "Download the self-hosted basemap (Protomaps tiles, Mapterhorn terrain, "
        "fonts and sprites) into BASEMAP_DIR. Run in the osmworker container, "
        "which has the pmtiles CLI."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-terrain",
            action="store_true",
            help="Leave terrain.pmtiles as it is; it does not need refreshing.",
        )
        parser.add_argument(
            "--skip-assets",
            action="store_true",
            help="Leave the fonts and sprites as they are.",
        )

    def handle(self, *args, skip_terrain, skip_assets, **options):
        basemap.fetch_all(terrain=not skip_terrain, assets=not skip_assets)
        ready = f"Basemap ready in {basemap.basemap_dir()}"
        self.stdout.write(self.style.SUCCESS(ready))
