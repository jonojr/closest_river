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
from pathlib import Path

import requests
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

# The replication script has a /usr/bin/python3 shebang and needs pyosmium 3.x
# (it imports osmium.WriteHandler, removed in 4.x). It must NOT be run under
# the application interpreter, which pins osmium 4.x.
REPLICATION_BIN = "/usr/bin/osm2pgsql-replication"

DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024

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


def _run(command: list[str], *, description: str) -> None:
    """Run a subprocess, streaming its output into the log."""
    logger.info("%s: %s", description, _redacted(command))
    try:
        process = subprocess.run(  # noqa: S603
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        msg = f"{command[0]} is not installed - is this the osmworker container?"
        raise OsmPipelineError(msg) from error
    except subprocess.CalledProcessError as error:
        logger.error(  # noqa: TRY400
            "%s failed (exit %s):\n%s",
            description,
            error.returncode,
            error.stderr,
        )
        msg = f"{description} failed with exit code {error.returncode}"
        raise OsmPipelineError(msg) from error

    if process.stderr:
        # osm2pgsql writes its progress to stderr even on success.
        logger.info("%s output:\n%s", description, process.stderr.strip())


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

    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=".planet-download-",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with requests.get(url, stream=True, timeout=(30, 600)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    temporary.write(chunk)
                    digest.update(chunk)
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
    logger.info(
        "Downloaded %s (%.1f GB)",
        destination,
        destination.stat().st_size / 1e9,
    )
    return destination


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
            "--overwrite",
            "--output",
            str(destination),
            str(source),
            "w/waterway=river,stream",
            "r/type=waterway",
        ],
        description="osmium tags-filter",
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


def free_space(path: Path | None = None) -> int:
    return shutil.disk_usage(path or data_dir()).free
