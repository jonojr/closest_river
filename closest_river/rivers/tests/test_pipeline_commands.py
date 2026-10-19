"""The exact argv handed to osm2pgsql.

These caught a real failure: import_pbf built a command with no connection
arguments at all, so osm2pgsql fell back to a local Unix socket that does not
exist in the worker container. The integration tests missed it because they
pass --database themselves.
"""

from pathlib import Path

import pytest
from django.conf import settings

from closest_river.rivers import osm_pipeline


@pytest.fixture
def captured(monkeypatch):
    """Capture the argv instead of running anything."""
    calls = []
    monkeypatch.setattr(
        osm_pipeline,
        "_run",
        # Not inlinable: the lambda exists to swallow `description`.
        lambda command, *, description: calls.append(command),  # noqa: PLW0108
    )
    monkeypatch.setattr(osm_pipeline, "create_schemas", lambda *schemas: None)
    # Both are the pipeline's only database access; stubbing them keeps these
    # argv tests off the database. The guard has its own tests.
    monkeypatch.setattr(osm_pipeline, "require_populated", lambda schema, **_: 1)
    return calls


def test_import_passes_a_database_connection(captured):
    osm_pipeline.import_pbf(Path("/data/waterways.osm.pbf"), schema="osm_staging")

    command = captured[0]
    assert "--database" in command, (
        "without this osm2pgsql tries a local Unix socket and cannot connect"
    )
    assert command[command.index("--database") + 1].startswith("postgresql://")
    assert "--schema=osm_staging" in command
    assert "--slim" in command, "the middle tables are what make replication possible"


def test_replication_update_does_not_repeat_what_the_script_adds(captured):
    """osm2pgsql-replication builds the osm2pgsql command itself.

    It always adds --append and --slim, and appends its own --schema and
    --middle-schema after the passthrough arguments. Repeating either schema
    option here made osm2pgsql 2.1.1 exit with "--schema: At most 1 required
    but received 2", which failed the daily sync.
    """
    osm_pipeline.replication_update(schema="osm")

    command = captured[0]
    after_separator = command[command.index("--") + 1 :]
    assert "--slim" not in after_separator
    assert "--append" not in after_separator
    assert not [
        argument
        for argument in after_separator
        if argument.startswith(("--schema", "--middle-schema"))
    ]
    # The flex output and its style still have to be handed through.
    assert "--output=flex" in after_separator
    assert f"--style={settings.OSM2PGSQL_STYLE}" in after_separator


def test_the_database_password_is_never_logged():
    """These commands take the DSN as an argument, so it reaches the log."""
    command = [
        "osm2pgsql",
        "--database",
        "postgresql://someuser:sup3rs3cret@postgres:5432/closest_river",
        "/data/waterways.osm.pbf",
    ]

    logged = osm_pipeline._redacted(command)

    assert "sup3rs3cret" not in logged
    assert "postgresql://someuser:***@postgres:5432/closest_river" in logged


def test_replication_commands_target_the_right_schemas(captured):
    osm_pipeline.replication_init(Path("/data/waterways.osm.pbf"), schema="osm")
    osm_pipeline.replication_update(schema="osm")

    for command in captured:
        assert command[0] == osm_pipeline.REPLICATION_BIN
        assert "--middle-schema" in command
        # The cursor lives in the middle schema; pointing it elsewhere would
        # silently restart replication from scratch.
        middle = command[command.index("--middle-schema") + 1]
        assert middle == "osm_middle"
