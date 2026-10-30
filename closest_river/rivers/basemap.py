"""Fetching the self-hosted basemap.

Everything the map's background needs, written to BASEMAP_DIR, which nginx
serves as /tiles/:

* ``basemap.pmtiles`` — Protomaps vector tiles from the latest daily planet
  build: downloaded whole for the planet, or cut down to BASEMAP_BBOX.
* ``terrain.pmtiles`` — Mapterhorn elevation, for the Terrain basemap's
  hillshade and contours.
* ``fonts/`` and ``sprites/v4/`` — the glyphs and icons the Protomaps styles
  reference, from protomaps/basemaps-assets.

``pmtiles extract`` reads only the parts of the remote archive it needs, over
HTTP range requests, so there is nothing to generate and no planet-sized
download for a regional extract. Every file is written beside its target and
renamed over it, so nginx never serves a half-written archive.

Only the osmworker container has the pmtiles CLI; see the ``osm-tools`` stage
in compose/{local,production}/django/Dockerfile.
"""

import logging
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

import requests
from django.conf import settings

from closest_river.rivers.osm_pipeline import OsmPipelineError
from closest_river.rivers.osm_pipeline import _run
from closest_river.rivers.osm_pipeline import download

logger = logging.getLogger(__name__)

PMTILES_BIN = "pmtiles"

BASEMAP_FILE = "basemap.pmtiles"
TERRAIN_FILE = "terrain.pmtiles"

PROTOMAPS_BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
PROTOMAPS_BUILD_URL = "https://build.protomaps.com/{key}"
MAPTERHORN_URL = "https://download.mapterhorn.com/planet.pmtiles"
# The deepest zoom in the Protomaps daily builds.
PROTOMAPS_MAXZOOM = 15
ASSETS_URL = "https://codeload.github.com/protomaps/basemaps-assets/tar.gz/{ref}"

# pmtiles extract does not retry a dropped connection, and cannot resume:
# the planet basemap once failed 20 minutes in on an HTTP/2 stream reset.
EXTRACT_ATTEMPTS = 3
EXTRACT_RETRY_SECONDS = 60

# What the styles use from basemaps-assets; the repository also carries the
# older v3 sprites.
ASSET_DIRS = ("fonts", "sprites/v4")


def basemap_dir() -> Path:
    path = Path(settings.BASEMAP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_protomaps_build() -> str:
    """URL of the newest daily planet build."""
    try:
        response = requests.get(PROTOMAPS_BUILDS_URL, timeout=30)
        response.raise_for_status()
        builds = response.json()
    except (requests.RequestException, ValueError) as error:
        msg = f"Could not list Protomaps builds from {PROTOMAPS_BUILDS_URL}"
        raise OsmPipelineError(msg) from error
    if not builds:
        msg = f"{PROTOMAPS_BUILDS_URL} lists no builds"
        raise OsmPipelineError(msg)
    # Keys are dated (20261024.pmtiles), so the newest sorts last.
    key = max(build["key"] for build in builds)
    return PROTOMAPS_BUILD_URL.format(key=key)


def extract(source: str, destination: Path, *, maxzoom: int, bbox: str = "") -> Path:
    """Cut an archive down to a zoom range and, optionally, a bounding box."""
    temporary = destination.with_name(f".{destination.name}.partial")
    temporary.unlink(missing_ok=True)

    command = [PMTILES_BIN, "extract", source, str(temporary), f"--maxzoom={maxzoom}"]
    if bbox:
        command.append(f"--bbox={bbox}")
    for attempt in range(1, EXTRACT_ATTEMPTS + 1):
        try:
            _run(command, description=f"Extracting {destination.name}")
            break
        except OsmPipelineError:
            temporary.unlink(missing_ok=True)
            if attempt == EXTRACT_ATTEMPTS:
                raise
            logger.warning(
                "Extracting %s failed (attempt %s of %s); retrying in %ss",
                destination.name,
                attempt,
                EXTRACT_ATTEMPTS,
                EXTRACT_RETRY_SECONDS,
            )
            time.sleep(EXTRACT_RETRY_SECONDS)

    temporary.replace(destination)
    logger.info(
        "Extracted %s (%.1f GB)",
        destination,
        destination.stat().st_size / 1e9,
    )
    return destination


def fetch_basemap(*, source: str | None = None) -> Path:
    source = source or latest_protomaps_build()
    destination = basemap_dir() / BASEMAP_FILE
    if not settings.BASEMAP_BBOX and settings.BASEMAP_MAXZOOM >= PROTOMAPS_MAXZOOM:
        # The whole build, so there is nothing to cut: a plain download, which
        # unlike pmtiles extract can resume after a dropped connection.
        return download(source, destination)
    return extract(
        source,
        destination,
        maxzoom=settings.BASEMAP_MAXZOOM,
        bbox=settings.BASEMAP_BBOX,
    )


def fetch_terrain() -> Path:
    return extract(
        MAPTERHORN_URL,
        basemap_dir() / TERRAIN_FILE,
        maxzoom=settings.BASEMAP_TERRAIN_MAXZOOM,
        bbox=settings.BASEMAP_BBOX,
    )


def _asset_members(archive: tarfile.TarFile):
    """The archive's fonts and sprites, with the top-level folder stripped.

    GitHub wraps everything in basemaps-assets-<ref>/. Anything else, and any
    path that would escape the destination, is skipped.
    """
    for member in archive.getmembers():
        _, _, path = member.name.partition("/")
        if not path.startswith(ASSET_DIRS) or not (member.isfile() or member.isdir()):
            continue
        if Path(path).is_absolute() or ".." in Path(path).parts:
            continue
        member.name = path
        yield member


def fetch_assets(*, ref: str | None = None) -> Path:
    """Fonts and sprites, swapped in as a whole so a style never mixes versions."""
    ref = ref or settings.BASEMAP_ASSETS_REF
    url = ASSETS_URL.format(ref=ref)
    root = basemap_dir()

    with tempfile.TemporaryDirectory(dir=root, prefix=".assets-") as staging:
        staging_path = Path(staging)
        tarball = staging_path / "assets.tar.gz"
        logger.info("Downloading basemap fonts and sprites from %s", url)
        try:
            with requests.get(url, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                with tarball.open("wb") as file:
                    for chunk in response.iter_content(1024 * 1024):
                        file.write(chunk)
        except requests.RequestException as error:
            msg = f"Failed to download {url}"
            raise OsmPipelineError(msg) from error

        unpacked = staging_path / "unpacked"
        with tarfile.open(tarball) as archive:
            archive.extractall(unpacked, members=_asset_members(archive), filter="data")

        for name in ("fonts", "sprites"):
            if not (unpacked / name).is_dir():
                msg = f"{url} has no {name}/ directory"
                raise OsmPipelineError(msg)
            # Not atomic across both directories, but each one is swapped
            # whole, and a refresh only changes them when the ref changes.
            target = root / name
            old = staging_path / f"old-{name}"
            if target.exists():
                target.replace(old)
            (unpacked / name).replace(target)
            shutil.rmtree(old, ignore_errors=True)

    logger.info("Installed basemap fonts and sprites from %s", ref)
    return root


def fetch_all(*, terrain: bool = True, assets: bool = True) -> None:
    """Everything the basemaps need. Terrain and the assets rarely change, so
    a refresh of the vector tiles alone can skip them."""
    if assets:
        fetch_assets()
    fetch_basemap()
    if terrain:
        fetch_terrain()
