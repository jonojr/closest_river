"""The pipeline logs a long-running command's output while it runs."""

import logging
import sys

import pytest

from closest_river.rivers import osm_pipeline


def _python(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def test_output_from_both_streams_is_logged(caplog):
    caplog.set_level(logging.INFO, logger=osm_pipeline.__name__)

    osm_pipeline._run(
        _python("import sys; print('to stdout'); print('to stderr', file=sys.stderr)"),
        description="example",
    )

    assert "example | to stdout" in caplog.messages
    assert "example | to stderr" in caplog.messages
    assert caplog.messages[-1].startswith("example finished in ")


def test_progress_updates_are_throttled(caplog):
    """osm2pgsql rewrites its progress line with carriage returns many times a
    second; only the first within each interval is logged."""
    caplog.set_level(logging.INFO, logger=osm_pipeline.__name__)

    osm_pipeline._run(
        _python(
            "import sys\n"
            "for n in range(100):\n"
            "    sys.stderr.write(f'\\rProcessing: Node({n}k 1.0k/s)')\n"
            "print('\\nAll done')",
        ),
        description="example",
    )

    progress = [m for m in caplog.messages if m.startswith("example | Processing:")]
    assert progress == ["example | Processing: Node(0k 1.0k/s)"]
    assert "example | All done" in caplog.messages


def test_a_failure_reports_the_last_output(caplog):
    with pytest.raises(osm_pipeline.OsmPipelineError, match="exit code 3"):
        osm_pipeline._run(
            _python("import sys; print('what went wrong'); sys.exit(3)"),
            description="example",
        )

    failure = caplog.records[-1]
    assert failure.levelno == logging.ERROR
    assert "what went wrong" in failure.getMessage()


def test_a_missing_binary_says_which_container_to_use():
    with pytest.raises(osm_pipeline.OsmPipelineError, match="osmworker"):
        osm_pipeline._run(["no-such-binary"], description="example")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(12.4, "12s"), (249, "4m 09s"), (3900, "1h 05m")],
)
def test_format_duration(seconds, expected):
    assert osm_pipeline.format_duration(seconds) == expected
