"""Shelling out to osmium and osm2pgsql.

The pipeline has two entry points:

* :func:`full_import` — download the planet, cut it down to waterways, import
  into a fresh schema and swap it in. Slow, run once and then periodically to
  reset accumulated drift.
* :func:`replication_update` — apply the OSM replication diffs published since
  the last run. Fast, run daily.

Only the osmworker container has these binaries; see the ``osm-tools`` stage in
compose/{local,production}/django/Dockerfile.
"""

import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path

import requests
from django.conf import settings
from django.db import connection

from closest_river.rivers.models import intermittent_tags_sql
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL
from closest_river.rivers.vector_layers import TILE_BUFFER
from closest_river.rivers.vector_layers import TILE_EXTENT

logger = logging.getLogger(__name__)

# The replication script has a /usr/bin/python3 shebang and needs pyosmium 3.x
# (it imports osmium.WriteHandler, removed in 4.x). It must NOT be run under
# the application interpreter, which pins osmium 4.x.
REPLICATION_BIN = "/usr/bin/osm2pgsql-replication"

DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# How often the long-running steps report how far they have got.
DOWNLOAD_PROGRESS_SECONDS = 60
OSM2PGSQL_PROGRESS_SECONDS = 30

# osm2pgsql rewrites one progress line several times a second
# ("Processing: Node(1234k 567.8k/s) Way(...)"); these are throttled.
_PROGRESS_LINE = re.compile(r"Processing: ")

# Output kept for the error message when a command fails.
_OUTPUT_TAIL_LINES = 50

# The table a schema's health is judged by: it is what the closest-river query
# reads, and no schema is usable without it.
CANONICAL_TABLE = "waterway_section"


class OsmPipelineError(Exception):
    pass


_DSN_PASSWORD = re.compile(r"(?P<scheme>postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@")


def _redacted(command: list[str]) -> str:
    """Command line with any connection-string password masked.

    These commands take the DSN as an argument, so logging them verbatim would
    write the database password into the log on every run.
    """
    return " ".join(_DSN_PASSWORD.sub(r"\g<scheme>***@", part) for part in command)


def format_duration(seconds: float) -> str:
    """'1h 05m', '4m 09s' or '12s'."""
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _run(command: list[str], *, description: str) -> None:
    """Run a subprocess, logging its output line by line as it arrives.

    An import runs for hours, so its output has to reach the log while it
    runs, not once it exits. Both streams are merged: osm2pgsql and osmium
    write their progress to stderr even on success.
    """
    logger.info("%s: %s", description, _redacted(command))
    started = time.monotonic()
    tail = deque(maxlen=_OUTPUT_TAIL_LINES)
    last_progress = None
    try:
        # Text mode reads osm2pgsql's carriage-return progress updates as lines.
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as error:
        msg = f"{command[0]} is not installed - is this the osmworker container?"
        raise OsmPipelineError(msg) from error

    with process:
        for raw in process.stdout:
            line = raw.strip()
            if not line:
                continue
            tail.append(line)
            if _PROGRESS_LINE.search(line):
                now = time.monotonic()
                if last_progress and now - last_progress < OSM2PGSQL_PROGRESS_SECONDS:
                    continue
                last_progress = now
            logger.info("%s | %s", description, line)

    elapsed = format_duration(time.monotonic() - started)
    if process.returncode:
        logger.error(
            "%s failed (exit %s) after %s; last output:\n%s",
            description,
            process.returncode,
            elapsed,
            "\n".join(tail),
        )
        msg = f"{description} failed with exit code {process.returncode}"
        raise OsmPipelineError(msg)
    logger.info("%s finished in %s", description, elapsed)


def database_dsn() -> str:
    db = settings.DATABASES["default"]
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )


def data_dir() -> Path:
    path = Path(settings.OSM_DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_planet(*, url: str | None = None, destination: Path | None = None) -> Path:
    """Stream the planet PBF to disk and verify its published MD5.

    Streamed in chunks and written to a temporary file first: the file is tens
    of gigabytes, so it can neither be held in memory nor left half-written
    where a later step would treat it as complete.
    """
    url = url or settings.OSM_PLANET_URL
    destination = destination or (data_dir() / "planet-latest.osm.pbf")

    logger.info("Downloading %s to %s", url, destination)
    digest = hashlib.md5()  # noqa: S324  # MD5 is what planet.openstreetmap.org publishes
    started = time.monotonic()

    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=".planet-download-",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with requests.get(url, stream=True, timeout=(30, 600)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                last_report = started
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    temporary.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= DOWNLOAD_PROGRESS_SECONDS:
                        last_report = now
                        _log_download_progress(received, total, now - started)
        except requests.RequestException as error:
            temporary_path.unlink(missing_ok=True)
            msg = f"Failed to download {url}"
            raise OsmPipelineError(msg) from error

    expected = _fetch_expected_md5(f"{url}.md5")
    if expected and digest.hexdigest() != expected:
        temporary_path.unlink(missing_ok=True)
        msg = (
            f"Checksum mismatch for {url}: "
            f"expected {expected}, got {digest.hexdigest()}"
        )
        raise OsmPipelineError(msg)

    temporary_path.replace(destination)
    elapsed = time.monotonic() - started
    size = destination.stat().st_size
    logger.info(
        "Downloaded %s (%.1f GB) in %s, %.1f MB/s",
        destination,
        size / 1e9,
        format_duration(elapsed),
        size / 1e6 / max(elapsed, 1),
    )
    return destination


def _log_download_progress(received: int, total: int, elapsed: float) -> None:
    rate = received / max(elapsed, 1)
    if total:
        logger.info(
            "Downloaded %.1f of %.1f GB (%d%%), %.1f MB/s, about %s left",
            received / 1e9,
            total / 1e9,
            100 * received // total,
            rate / 1e6,
            format_duration((total - received) / max(rate, 1)),
        )
    else:
        logger.info("Downloaded %.1f GB, %.1f MB/s", received / 1e9, rate / 1e6)


def _fetch_expected_md5(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Could not fetch checksum from %s; skipping verification", url)
        return None
    return response.text.split()[0].strip()


def filter_waterways(source: Path, destination: Path | None = None) -> Path:
    """Cut a PBF down to waterways and the objects they reference.

    Keeping the import filtered is what makes the osm2pgsql middle tables tens
    of GB rather than hundreds. Referenced objects (member ways, then their
    nodes) are included by default; ``-R`` would opt out and leave every way
    without geometry.
    """
    destination = destination or (source.parent / "waterways.osm.pbf")
    _run(
        [
            "osmium",
            "tags-filter",
            # Timestamped messages as each pass starts and ends.
            "--verbose",
            "--overwrite",
            "--output",
            str(destination),
            str(source),
            "w/waterway=river,stream",
            "r/type=waterway",
        ],
        description="osmium tags-filter",
    )
    logger.info(
        "Filtered %s down to %s (%.2f GB)",
        source.name,
        destination.name,
        destination.stat().st_size / 1e9,
    )
    return destination


def _flex_output_args() -> list[str]:
    return [
        "--output=flex",
        f"--style={settings.OSM2PGSQL_STYLE}",
    ]


def _osm2pgsql_output_args(schema: str) -> list[str]:
    """Flex output plus the schemas, for a direct osm2pgsql call.

    Not for use after `--` in an osm2pgsql-replication command: that appends
    its own --schema/--middle-schema to whatever it is handed, and osm2pgsql
    2.x rejects either option given twice.
    """
    return [
        *_flex_output_args(),
        f"--schema={schema}",
        f"--middle-schema={settings.OSM_MIDDLE_SCHEMA}",
    ]


def _osm2pgsql_db_args() -> list[str]:
    """Connection arguments for a direct osm2pgsql call.

    Without these osm2pgsql falls back to a local Unix socket, which does not
    exist in the worker container. Not needed after `--` in an
    osm2pgsql-replication command: that hands its own connection through.
    """
    return ["--database", database_dsn()]


def _section_count(cursor, schema: str) -> int | None:
    """Rows in the schema's canonical table, or None if it does not exist."""
    cursor.execute("SELECT to_regclass(%s)", [f"{schema}.{CANONICAL_TABLE}"])
    if cursor.fetchone()[0] is None:
        return None
    cursor.execute(f'SELECT count(*) FROM "{schema}"."{CANONICAL_TABLE}"')
    return cursor.fetchone()[0]


def require_populated(schema: str, *, action: str) -> int:
    """Refuse to act on a schema that holds no waterways.

    Both callers exist because the pipeline lost a dataset this way: an import
    that produced nothing was still swapped in, and the daily update then
    "succeeded" against the empty schema it left behind — osm2pgsql --append
    creates any output table it finds missing, so nothing ever failed loudly.
    """
    with connection.cursor() as cursor:
        count = _section_count(cursor, schema)

    if count is None:
        msg = (
            f"Refusing to {action}: {schema}.{CANONICAL_TABLE} does not exist. "
            f"Schema {schema} has never been imported into."
        )
        raise OsmPipelineError(msg)
    if count == 0:
        msg = (
            f"Refusing to {action}: {schema}.{CANONICAL_TABLE} is empty. "
            f"The import that built {schema} produced no waterways."
        )
        raise OsmPipelineError(msg)
    return count


def create_schemas(*schemas: str) -> None:
    with connection.cursor() as cursor:
        for schema in schemas:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def import_pbf(pbf: Path, *, schema: str) -> None:
    """Build a fresh set of tables from a PBF (osm2pgsql --create)."""
    create_schemas(schema, settings.OSM_MIDDLE_SCHEMA)
    _run(
        [
            "osm2pgsql",
            "--create",
            # --slim keeps the middle tables that make replication possible.
            "--slim",
            f"--cache={settings.OSM2PGSQL_CACHE_MB}",
            # Not a terminal, so 'auto' would log no progress at all.
            "--log-progress=true",
            *_osm2pgsql_db_args(),
            *_osm2pgsql_output_args(schema),
            str(pbf),
        ],
        description="osm2pgsql --create",
    )


def _replication_db_args(schema: str) -> list[str]:
    return [
        "--database",
        database_dsn(),
        "--schema",
        schema,
        "--middle-schema",
        settings.OSM_MIDDLE_SCHEMA,
    ]


def replication_init(pbf: Path, *, schema: str) -> None:
    """Set the replication cursor from the timestamp in the imported file."""
    _run(
        [
            REPLICATION_BIN,
            "init",
            *_replication_db_args(schema),
            "--osm-file",
            str(pbf),
        ],
        description="osm2pgsql-replication init",
    )


def replication_update(*, schema: str | None = None) -> None:
    """Apply every diff published since the last run.

    The cursor lives in the osm2pgsql properties table and is written in the
    same transaction as the data, so an interrupted run rolls back rather than
    silently skipping a day.
    """
    schema = schema or settings.OSM_SCHEMA
    require_populated(schema, action="apply diffs")
    _run(
        [
            REPLICATION_BIN,
            "update",
            *_replication_db_args(schema),
            f"--max-diff-size={settings.OSM_MAX_DIFF_SIZE_MB}",
            # Everything after `--` is handed to osm2pgsql. Do NOT repeat what
            # the replication script adds itself: --append and --slim, and the
            # --schema/--middle-schema passed above. osm2pgsql 2.x fails with
            # "--schema: At most 1 required but received 2" on a repeat.
            "--",
            f"--cache={settings.OSM2PGSQL_CACHE_MB}",
            *_flex_output_args(),
        ],
        description="osm2pgsql-replication update",
    )


def swap_schema(*, staging: str, live: str) -> None:
    """Atomically promote a freshly built schema.

    Both renames happen in one transaction, so readers see either the old
    schema or the new one and never a half-swapped state.

    The staging schema is checked before anything is dropped: promoting an
    empty one would replace the live data with nothing *and* destroy the
    retired copy that is the only way back.
    """
    retired = f"{live}_retired"
    count = require_populated(staging, action=f"promote {staging} to {live}")
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{retired}" CASCADE')
        if _schema_exists(cursor, live):
            cursor.execute(f'ALTER SCHEMA "{live}" RENAME TO "{retired}"')
        cursor.execute(f'ALTER SCHEMA "{staging}" RENAME TO "{live}"')
    logger.info("Promoted schema %s to %s (%s sections)", staging, live, count)


def _schema_exists(cursor, schema: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        [schema],
    )
    return cursor.fetchone() is not None


def analyze_schema(schema: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename FROM pg_tables WHERE schemaname = %s
            """,
            [schema],
        )
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(f'ANALYZE "{schema}"."{table}"')
    logger.info("Analyzed %s table(s) in schema %s", len(tables), schema)


# Half the width of the Web Mercator world, in metres.
MERCATOR_HALF_WORLD_M = 20037508.342789244


def _tiles_covering_sql(geometry: str) -> str:
    """(zoom, x, y) of every cached tile a 3857 geometry is drawn in.

    The same tiles osm2pgsql's expire output would list, from z1 (it cannot
    emit z0, which tile_cache always evicts) to the highest cached zoom. The
    bounding box is widened by the tile buffer, because a line just outside a
    tile is still drawn in its buffer.
    """
    half = MERCATOR_HALF_WORLD_M
    size = f"({2 * half} / (1 << z))"
    pad = TILE_BUFFER / TILE_EXTENT
    last = "((1 << z) - 1)"

    def tile_range(low: str, high: str) -> str:
        return (
            f"generate_series(GREATEST(floor({low} / {size} - {pad})::int, 0), "
            f"LEAST(floor({high} / {size} + {pad})::int, {last}))"
        )

    x_range = tile_range(
        f"(ST_XMin({geometry}) + {half})",
        f"(ST_XMax({geometry}) + {half})",
    )
    y_range = tile_range(
        f"({half} - ST_YMax({geometry}))",
        f"({half} - ST_YMin({geometry}))",
    )
    return f"""
        SELECT DISTINCT z, x, y
        FROM flipped,
             generate_series(1, {CACHE_BELOW_ZOOM_LEVEL - 1}) AS z,
             LATERAL {x_range} AS x,
             LATERAL {y_range} AS y
    """  # noqa: S608


def apply_river_intermittency(schema: str, *, expire: bool) -> int:
    """Draw every member of a river relation the way its river is drawn.

    Tagged way by way, a mostly permanent river breaks up into dashes wherever
    a stretch happens to be intermittent. So each member's
    waterway_tile.intermittent is replaced with its river's verdict, which is
    River.is_mostly_intermittent() in SQL: the relation's own tag, or more
    than half its sections.

    This cannot be done in waterways.lua. The verdict depends on every member
    way, and an append only processes the ones that changed. So it is reapplied
    in full after every import and sync: waterways.lua writes each way's own
    value, and this corrects the members. Only rows whose value changes are
    written.

    With ``expire``, the tiles of every way that changed are added to the
    expire table for the sync to evict: retagging one way can restyle a whole
    river, far beyond anything osm2pgsql expired. Not wanted after a full
    import, which strands the whole cache anyway.

    Returns the number of ways restyled.
    """
    tables = {
        name: f'"{schema}"."{name}"'
        for name in ("waterway_section", "waterway_relation", "waterway_tile")
    }
    expire_cte = (
        f""",
        expired AS (
            INSERT INTO "{schema}".tile_expire (zoom, x, y)
            {_tiles_covering_sql("geometry")}
            ON CONFLICT DO NOTHING
        )"""
        if expire
        else ""
    )
    sql = f"""
        WITH verdict AS (
            SELECT s.river_osm_id,
                   bool_or({intermittent_tags_sql("r.tags")})
                   OR count(*) FILTER (WHERE {intermittent_tags_sql("s.tags")})
                      * 2 > count(*) AS intermittent
            FROM {tables["waterway_section"]} s
            -- Inner, so a section whose relation is missing keeps its own
            -- value, as it does on the result card.
            JOIN {tables["waterway_relation"]} r ON r.osm_id = s.river_osm_id
            GROUP BY s.river_osm_id
        ),
        flipped AS (
            UPDATE {tables["waterway_tile"]} t
            SET intermittent = v.intermittent
            FROM {tables["waterway_section"]} s
            JOIN verdict v ON v.river_osm_id = s.river_osm_id
            WHERE t.osm_way_id = s.osm_way_id
              AND t.intermittent <> v.intermittent
            RETURNING t.geometry
        ){expire_cte}
        SELECT count(*) FROM flipped
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql)
        restyled = cursor.fetchone()[0]
    logger.info("Restyled %s way(s) in %s to match their river", restyled, schema)
    return restyled


def free_space(path: Path | None = None) -> int:
    return shutil.disk_usage(path or data_dir()).free
