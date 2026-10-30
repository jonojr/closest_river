"""Fetching the self-hosted basemap, with the pmtiles CLI and network stubbed."""

import io
import tarfile

import pytest

from closest_river.rivers import basemap
from closest_river.rivers.osm_pipeline import OsmPipelineError


@pytest.fixture(autouse=True)
def basemap_dir(settings, tmp_path):
    settings.BASEMAP_DIR = str(tmp_path)
    settings.BASEMAP_BBOX = "112.9,-43.9,154.0,-9.0"
    settings.BASEMAP_MAXZOOM = 15
    settings.BASEMAP_TERRAIN_MAXZOOM = 11
    return tmp_path


@pytest.fixture
def pmtiles(monkeypatch):
    """Record each pmtiles command, writing its output file as the CLI would."""
    calls = []

    def run(command, *, description):
        calls.append(command)
        (basemap.Path(command[3])).write_bytes(b"tiles")

    monkeypatch.setattr(basemap, "_run", run)
    return calls


class FakeResponse:
    def __init__(self, *, json=None, content=b""):
        self._json = json
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def json(self):
        return self._json

    def iter_content(self, chunk_size):
        yield self._content


def test_the_newest_daily_build_is_used(monkeypatch):
    builds = [{"key": "20261023.pmtiles"}, {"key": "20261024.pmtiles"}]
    monkeypatch.setattr(
        basemap.requests,
        "get",
        lambda url, **kwargs: FakeResponse(json=builds),
    )

    assert basemap.latest_protomaps_build() == (
        "https://build.protomaps.com/20261024.pmtiles"
    )


def test_basemap_is_cut_to_the_configured_area_and_zoom(pmtiles, basemap_dir):
    path = basemap.fetch_basemap(source="https://example.com/planet.pmtiles")

    assert pmtiles == [
        [
            "pmtiles",
            "extract",
            "https://example.com/planet.pmtiles",
            str(basemap_dir / ".basemap.pmtiles.partial"),
            "--maxzoom=15",
            "--bbox=112.9,-43.9,154.0,-9.0",
        ],
    ]
    assert path == basemap_dir / "basemap.pmtiles"
    assert path.read_bytes() == b"tiles"
    assert not (basemap_dir / ".basemap.pmtiles.partial").exists()


def test_the_whole_planet_is_downloaded_rather_than_extracted(
    monkeypatch,
    pmtiles,
    settings,
    basemap_dir,
):
    """Nothing to cut, and a download can resume where pmtiles extract
    starts again: the planet build is over 120 GB."""
    settings.BASEMAP_BBOX = ""
    downloads = []
    monkeypatch.setattr(
        basemap,
        "download",
        lambda url, destination: downloads.append((url, destination)),
    )

    basemap.fetch_basemap(source="https://example.com/planet.pmtiles")

    assert downloads == [
        ("https://example.com/planet.pmtiles", basemap_dir / "basemap.pmtiles"),
    ]
    assert pmtiles == []


def test_no_bbox_means_the_whole_planet(pmtiles, settings):
    settings.BASEMAP_BBOX = ""

    basemap.fetch_terrain()

    assert pmtiles[0][2] == basemap.MAPTERHORN_URL
    assert "--maxzoom=11" in pmtiles[0]
    assert not any(arg.startswith("--bbox") for arg in pmtiles[0])


@pytest.fixture
def no_retry_wait(monkeypatch):
    waits = []
    monkeypatch.setattr(basemap.time, "sleep", waits.append)
    return waits


def test_a_failed_extract_keeps_the_archive_being_served(
    monkeypatch,
    basemap_dir,
    no_retry_wait,
):
    live = basemap_dir / "basemap.pmtiles"
    live.write_bytes(b"old tiles")
    attempts = []

    def fail(command, *, description):
        attempts.append(command)
        basemap.Path(command[3]).write_bytes(b"half")
        raise OsmPipelineError(description)

    monkeypatch.setattr(basemap, "_run", fail)

    with pytest.raises(OsmPipelineError):
        basemap.fetch_basemap(source="https://example.com/planet.pmtiles")

    assert len(attempts) == basemap.EXTRACT_ATTEMPTS
    assert live.read_bytes() == b"old tiles"
    assert not (basemap_dir / ".basemap.pmtiles.partial").exists()


def test_a_dropped_connection_is_retried(monkeypatch, basemap_dir, no_retry_wait):
    attempts = []

    def flaky(command, *, description):
        attempts.append(command)
        if len(attempts) == 1:
            basemap.Path(command[3]).write_bytes(b"half")
            msg = "stream error: stream ID 3; INTERNAL_ERROR"
            raise OsmPipelineError(msg)
        basemap.Path(command[3]).write_bytes(b"tiles")

    monkeypatch.setattr(basemap, "_run", flaky)

    path = basemap.fetch_basemap(source="https://example.com/planet.pmtiles")

    assert len(attempts) == 2
    assert no_retry_wait == [basemap.EXTRACT_RETRY_SECONDS]
    assert path.read_bytes() == b"tiles"


def _tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_assets_install_only_the_fonts_and_v4_sprites(monkeypatch, basemap_dir):
    tarball = _tarball(
        {
            "basemaps-assets-abc/fonts/Noto Sans Regular/0-255.pbf": b"glyphs",
            "basemaps-assets-abc/sprites/v4/light.json": b"{}",
            "basemaps-assets-abc/sprites/v3/light.json": b"old",
            "basemaps-assets-abc/README.md": b"readme",
            "basemaps-assets-abc/fonts/../../escape.txt": b"nope",
        },
    )
    monkeypatch.setattr(
        basemap.requests,
        "get",
        lambda url, **kwargs: FakeResponse(content=tarball),
    )
    (basemap_dir / "fonts").mkdir()
    (basemap_dir / "fonts" / "stale.pbf").write_bytes(b"stale")

    basemap.fetch_assets(ref="abc")

    assert (basemap_dir / "fonts/Noto Sans Regular/0-255.pbf").read_bytes() == (
        b"glyphs"
    )
    assert (basemap_dir / "sprites/v4/light.json").exists()
    assert not (basemap_dir / "sprites/v3").exists()
    assert not (basemap_dir / "README.md").exists()
    assert not (basemap_dir / "fonts" / "stale.pbf").exists()
    assert not (basemap_dir.parent / "escape.txt").exists()
    assert sorted(p.name for p in basemap_dir.iterdir()) == ["fonts", "sprites"]
