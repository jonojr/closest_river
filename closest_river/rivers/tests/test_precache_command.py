"""The precache_tiles command, which warms the whole tile cache by hand.

The sync only re-renders the tiles it evicted. After a deploy that changes the
cache keys every tile is cold, so this queues them all, in batches small enough
for each task to finish inside its time limit.
"""

from io import StringIO

import pytest
from django.core.management import CommandError
from django.core.management import call_command

from closest_river.rivers import tasks
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL


@pytest.fixture
def queued(monkeypatch):
    """Capture each batch instead of queueing it."""
    calls = []
    monkeypatch.setattr(
        tasks.precache_high_effort_tiles,
        "delay",
        lambda tiles, **kwargs: calls.append(tiles),
    )
    return calls


def _run(*args):
    out = StringIO()
    call_command("precache_tiles", *args, stdout=out)
    return out.getvalue()


def test_queues_every_tile_up_to_the_max_zoom_lowest_first(queued):
    _run("--max-zoom", "2")

    tiles = [tile for batch in queued for tile in batch]
    assert len(tiles) == 1 + 4 + 16
    assert tiles[0] == [0, 0, 0]
    assert [z for z, _, _ in tiles] == sorted(z for z, _, _ in tiles)
    assert len({tuple(tile) for tile in tiles}) == len(tiles), "no tile twice"
    assert all(0 <= x < 2**z and 0 <= y < 2**z for z, x, y in tiles)


def test_splits_the_tiles_into_batches(queued):
    output = _run("--max-zoom", "2", "--batch-size", "8")

    assert [len(batch) for batch in queued] == [8, 8, 5]
    assert "Queued 21 tiles (zoom 0-2) in 3 tasks" in output


def test_labels_each_batch_with_where_it_falls(monkeypatch):
    """So each task's log says how far through the whole run it is."""
    labels = []
    monkeypatch.setattr(
        tasks.precache_high_effort_tiles,
        "delay",
        lambda tiles, batch: labels.append(batch),
    )

    _run("--max-zoom", "2", "--batch-size", "8")

    assert labels == ["1 of 3", "2 of 3", "3 of 3"]


def test_defaults_to_the_sync_precache_zoom(queued, settings):
    settings.TILE_PRECACHE_MAX_ZOOM = 1

    _run()

    assert [tile for batch in queued for tile in batch] == [
        [0, 0, 0],
        [1, 0, 0],
        [1, 0, 1],
        [1, 1, 0],
        [1, 1, 1],
    ]


@pytest.mark.parametrize("max_zoom", [-1, CACHE_BELOW_ZOOM_LEVEL])
def test_refuses_zooms_that_are_never_cached(queued, max_zoom):
    with pytest.raises(CommandError, match="max zoom"):
        _run("--max-zoom", str(max_zoom))

    assert queued == []


def test_refuses_an_empty_batch(queued):
    with pytest.raises(CommandError, match="batch size"):
        _run("--batch-size", "0")
