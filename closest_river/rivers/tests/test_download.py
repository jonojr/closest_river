"""Downloading the planet files, with the network stubbed."""

import hashlib
from http import HTTPStatus

import pytest
import requests

from closest_river.rivers import osm_pipeline

BODY = b"0123456789" * 10


class FakeResponse:
    def __init__(self, body, *, status=HTTPStatus.OK, drop_after=None):
        self.status_code = status
        self.headers = {"Content-Length": str(len(body))}
        self._body = body
        self._drop_after = drop_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        if self._drop_after is None:
            yield self._body
            return
        yield self._body[: self._drop_after]
        msg = "Connection broken: stream ID 3; INTERNAL_ERROR"
        raise requests.exceptions.ChunkedEncodingError(msg)


@pytest.fixture
def server(monkeypatch):
    """Serve BODY, honouring Range, from a queue of planned responses."""
    requests_seen = []
    plan = []

    def get(url, *, headers, **kwargs):
        requests_seen.append(headers.get("Range"))
        behaviour = plan.pop(0) if plan else "ok"
        start = int(headers["Range"][6:-1]) if headers.get("Range") else 0
        if behaviour == "ignore-range":
            return FakeResponse(BODY)
        status = HTTPStatus.PARTIAL_CONTENT if start else HTTPStatus.OK
        if behaviour == "drop":
            return FakeResponse(BODY[start:], status=status, drop_after=30)
        return FakeResponse(BODY[start:], status=status)

    monkeypatch.setattr(osm_pipeline.requests, "get", get)
    monkeypatch.setattr(osm_pipeline.time, "sleep", lambda seconds: None)
    return plan, requests_seen


def test_a_dropped_connection_resumes_where_it_stopped(server, tmp_path):
    plan, requests_seen = server
    plan.extend(["drop", "drop"])
    destination = tmp_path / "planet.pbf"

    osm_pipeline.download(
        "https://example.com/planet.pbf",
        destination,
        md5=hashlib.md5(BODY).hexdigest(),  # noqa: S324
    )

    assert requests_seen == [None, "bytes=30-", "bytes=60-"]
    assert destination.read_bytes() == BODY
    assert not (tmp_path / ".planet.pbf.partial").exists()


def test_a_server_that_ignores_the_range_is_downloaded_again(server, tmp_path):
    plan, _ = server
    plan.extend(["drop", "ignore-range"])
    destination = tmp_path / "planet.pbf"

    osm_pipeline.download(
        "https://example.com/planet.pbf",
        destination,
        md5=hashlib.md5(BODY).hexdigest(),  # noqa: S324
    )

    assert destination.read_bytes() == BODY


def test_it_gives_up_after_the_last_attempt(server, tmp_path):
    plan, requests_seen = server
    plan.extend(["drop"] * osm_pipeline.DOWNLOAD_ATTEMPTS)

    with pytest.raises(osm_pipeline.OsmPipelineError, match="attempts"):
        osm_pipeline.download("https://example.com/planet.pbf", tmp_path / "p.pbf")

    assert len(requests_seen) == osm_pipeline.DOWNLOAD_ATTEMPTS
    assert list(tmp_path.iterdir()) == []


def test_a_checksum_mismatch_keeps_nothing(server, tmp_path):
    with pytest.raises(osm_pipeline.OsmPipelineError, match="Checksum mismatch"):
        osm_pipeline.download(
            "https://example.com/planet.pbf",
            tmp_path / "planet.pbf",
            md5="0" * 32,
        )

    assert list(tmp_path.iterdir()) == []
