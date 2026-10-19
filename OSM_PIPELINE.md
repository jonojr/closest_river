# OSM waterway pipeline

How waterway data gets into the database and stays current.

## Shape of it

```
planet-latest.osm.pbf  (~90 GB, downloaded once)
        │
        ├─ osmium tags-filter  w/waterway=river,stream  r/type=waterway
        │        └─ waterways.osm.pbf  (a few GB)
        │
        └─ osm2pgsql --create --slim --output=flex
                 style: compose/production/osm2pgsql/waterways.lua
                 └─ osm.*         waterway tables read by Django
                    osm_middle.*  slim middle: node/way/relation bookkeeping

daily:  osm2pgsql-replication update  ->  osm.tile_expire  ->  evict those cache keys
```

Everything runs in the **osmworker** container, the only image with `osm2pgsql`
and `osmium` installed and the only one with `/data` mounted. It serves a
dedicated Celery queue (`osm`) at concurrency 1.

## Who owns what

`osm2pgsql` owns the `osm` schema. The Django models in
`closest_river/rivers/models.py` are `managed = False` over those tables, so
**schema changes live in `waterways.lua`, not in migrations**. If you change one,
change the other — `test_importer_output_matches_the_django_models` will fail if
you forget, and so will the hand-written DDL in `rivers/tests/conftest.py`.

The one exception is `SyncState`, which is Django-managed and lives in `public`.

## What counts as a waterway

`osmium tags-filter` keeps ways tagged `waterway=river,stream` and relations
tagged `type=waterway`. `-R` is deliberately not passed, so a matched relation
drags its member ways and their nodes into the filtered PBF whatever *their* tags
say — which is what makes the next paragraph possible.

A traced watercourse routinely switches tag partway through: Bendigo Creek is a
`waterway=stream` relation whose seven ways through the CBD are tagged
`waterway=canal`, and dropping them left a gap in the middle of the creek. So a
member way of a river or stream relation is kept even when its own tag is one of
`canal`, `drain`, `ditch`, `tidal_channel` or `flowline`
(`PROMOTABLE_WATERWAY_TYPES` in `waterways.lua`). About 1.7% of the member ways
of river/stream relations are in this position.

Such a way is stored with its **relation's** `waterway` value, so the tile layers
and the closest-river query need no special case; its literal tag is still in the
`tags` column, which is what the popup shows. Only river and stream relations
promote: a `waterway=canal` or `waterway=drain` relation is recorded, and its
river/stream members linked, but it pulls nothing extra in. Anything outside that
list of values is skipped, which is what keeps untagged members and
`natural=water` polygons out of a relation's course.

Promotion is decided at import time, so widening that list requires a full
re-import, not a sync.

## Bootstrapping

```bash
docker compose -f docker-compose.production.yml run --rm osmworker \
  python manage.py shell -c \
  "from closest_river.rivers.tasks import full_import_osm_waterways; \
   full_import_osm_waterways.delay()"
```

This downloads the planet (verifying its published MD5), filters it, imports
into `osm_staging`, and swaps that schema into place atomically — so the live
site keeps serving the previous data until the new set is complete.

Expect hours, and budget disk accordingly:

| | rough size |
|---|---|
| planet PBF | ~90 GB |
| filtered waterways PBF | a few GB |
| `osm_middle` slim tables | tens of GB |
| `osm` output tables | tens of GB |

Filtering *before* importing is what keeps the middle tables to tens of GB
rather than the several hundred a full-planet slim import would need.

## Daily sync

Celery Beat runs `sync_osm_waterways` at 03:30 (`CELERY_BEAT_SCHEDULE`). It
applies every replication diff published since the last run, re-analyzes, and
evicts the affected tiles.

The replication cursor lives in osm2pgsql's properties table and is written **in
the same transaction as the data**, so an interrupted run rolls back and simply
reapplies the same diffs next time rather than skipping a day.

## Monthly rebuild — do not skip this

Feeding unfiltered planet diffs into a *filtered* import has one known gap: if a
pre-existing way is retagged into a waterway and its own nodes were not edited in
the same diff, osm2pgsql has no locations for those nodes and the way is missed.
Every other case — node moves, way edits, deletes, relation changes — is handled
correctly.

The same applies to a way that only *becomes* promotable later: a canal that is
added to a river relation in a diff, and which was not already in the filtered
extract, has no node locations in the middle either, so it stays missing until the
next full rebuild.

Re-running `full_import_osm_waterways` resets that drift to zero. Monthly is a
reasonable starting point; measure the actual miss rate before settling on a
cadence.

## The empty-schema failure

A rebuild that produced nothing was once swapped in over a good schema, and
because `osm2pgsql --append` creates any output table it finds missing, every
later daily sync "succeeded" against the empty schema it left behind — the site
served only the waterways that had been edited since. `swap_schema` now refuses
to promote a staging schema whose `waterway_section` is missing or empty, and
`replication_update` refuses to run against a live schema in that state.

The previous data survives one generation as `<schema>_retired`. Recovery is a
rebuild with `download=False` against the planet file already on the volume,
then a sync to catch up; rename the retired schema out of the way first, because
the swap drops it.

## Tiles

Two layers are served: `river-sections` (rivers, per-feature, clickable, drawn
heavier) and `stream-sections` (the dendritic wash, drawn lighter).

Zoom 0–10 are cached and rendered from `waterway_tile`, a Web Mercator copy of
every waterway. Above that, tiles are rendered per-request from
`waterway_section`. The threshold is `CACHE_BELOW_ZOOM_LEVEL`, which must stay
in step with `MAX_CACHED_ZOOM` in `waterways.lua` — that decides how far up tile
expiry is emitted, and an uncachable zoom that never expires would go stale.

Keeping ~640k Australian streams affordable at low zoom is done **without
dropping any of them**, because the dense network is the point of the map.
Three things do the work, none of which costs visible detail:

- **Snap to a one-pixel grid** first — sub-pixel vertices cannot be seen.
- **Merge touching lines** — OSM chops one creek into many ways, and merging
  concatenates rather than discards, removing per-feature protobuf overhead.
- **Tile extent 512** — `ST_AsMVTGeom` quantises to the tile grid anyway.

Measured on the densest tile in the country (the Victorian Alps), both layers
gzipped: 28 KB at z0, 105 KB at z2, 271 KB at z4 (the peak), 90 KB at z6,
103 KB at z8, 52 KB at z10. Rendering the same z8 tile naively was 1.4 MB.

Three results worth keeping, because they are all counter-intuitive:

- **Snapping, not simplification, is what makes this affordable.** Snapping
  makes coordinates repeat, which compresses well; Douglas-Peucker keeps full
  precision and produced a 1 MB z4 tile against snapping's 151 KB.
- **The grid must be sub-pixel or it shows.** Snapping moves vertices onto a
  rectangular lattice, so a coarse grid renders as visible right angles. Note
  MapLibre draws vector tiles across **512** CSS pixels, and a retina screen is
  two device pixels per CSS pixel — assuming 256 makes the grid 4x coarser than
  intended, which is very visible.
- **Tile extent barely matters** once snapping is doing the generalising (512
  and 4096 measured within 2%), so there is no reason to use a small one and
  add a second, coarser quantisation on top.

Because a merged geometry has no single way id, streams are not clickable below
the cache threshold; the front end guards for the missing `osm_way_id`.

Invalidation is targeted: osm2pgsql's expire output records exactly which tiles a
diff touched into `osm.tile_expire`, and the sync drains that table and deletes
only those cache keys. Two wrinkles:

- osm2pgsql rejects `minzoom = 0`, so zoom 0 never appears in the expire table.
  It is one tile that any change anywhere invalidates, so `tile_cache` always
  evicts it.
- Expiry only works in append mode. A full rebuild emits nothing, so it bumps
  `SyncState.data_version` instead, which is mixed into every cache key and
  strands the lot at once.

**Never call `cache.clear()`.** django-redis implements it as `FLUSHDB`.

Tiles are cached in their own Redis instance (`redis-cache`), separate from the
Celery broker, for two reasons: that `FLUSHDB`, and because the tile cache wants
`maxmemory` with `allkeys-lru` eviction — which on the broker would mean
silently dropping queued tasks.

`waterway_tile` is in Web Mercator (3857) rather than 4326 because flex only
permits tile expiry on Web Mercator geometry columns.

## Seeding a development database

No planet download needed — use a regional extract:

```bash
docker compose -f docker-compose.local.yml run --rm --entrypoint bash osmworker
# inside:
export PGHOST=$POSTGRES_HOST PGUSER=$POSTGRES_USER \
       PGPASSWORD=$POSTGRES_PASSWORD PGDATABASE=$POSTGRES_DB
cd /data
curl -sSLO https://download.geofabrik.de/australia-oceania/australia-latest.osm.pbf
osmium tags-filter -O -o waterways.osm.pbf australia-latest.osm.pbf \
  w/waterway=river,stream r/type=waterway
psql -c 'CREATE SCHEMA IF NOT EXISTS osm; CREATE SCHEMA IF NOT EXISTS osm_middle;'
osm2pgsql --create --slim --output=flex \
  --style=/app/compose/production/osm2pgsql/waterways.lua \
  --schema=osm --middle-schema=osm_middle waterways.osm.pbf
```

`liechtenstein-latest.osm.pbf` (3 MB) is a good smaller choice — it contains the
Rhine, so relations and roles are exercised.

## Tests

`closest_river/rivers/tests/test_osm_pipeline.py` runs the real importer against
a tiny XML fixture and asserts the behaviour that matters: relation roles and
ordering, streams reaching the tile table, deletes, retags, idempotence,
and — the reason osm2pgsql was chosen over ogr2ogr — that **a way's geometry is
rebuilt when one of its nodes moves in a diff that never mentions the way**.

They skip themselves outside the osmworker image; CI runs them in a dedicated
step.
