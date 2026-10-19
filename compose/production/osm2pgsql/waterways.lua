-- osm2pgsql flex style for closest_river.
--
-- This file OWNS the waterway tables. The Django models in
-- closest_river/rivers/models.py are `managed = False` over them, so a change
-- here is a schema change for the application: keep the two in step.
--
-- No table sets `schema` explicitly, deliberately. A per-table schema silently
-- overrides the --schema argument, which would make a staging import write
-- straight into the live schema instead of being swapped in atomically.
--
-- Requires osm2pgsql >= 2.0 for define_expire_output. Debian trixie ships
-- 2.1.1, which predates 2.3 — so do NOT use max_tiles_geometry,
-- max_tiles_overall or diff_expire here.

local WATERWAY_TYPES = { river = true, stream = true }

-- Members of a river or stream relation that are kept despite their own tag.
--
-- A traced watercourse routinely switches tag partway through: Bendigo Creek
-- becomes waterway=canal for seven ways through the CBD, which left a gap in
-- the middle of the creek. Roughly 1.7% of the member ways of river/stream
-- relations are tagged like this — mostly drain, then canal and ditch.
--
-- A promoted way is stored with its *relation's* waterway value, so the tile
-- layers and the closest-river query need no special case. The literal tag is
-- still in the tags column, which is what the popup reads.
--
-- Anything not listed here is skipped, and that is what keeps untagged members
-- and natural=water polygons out of a relation's course. Add a value if it
-- shows up as a real gap.
local PROMOTABLE_WATERWAY_TYPES = {
    canal = true,
    drain = true,
    ditch = true,
    tidal_channel = true,
    flowline = true,
}

-- The tile table is a Web Mercator copy of every waterway, used for all cached
-- zoom levels. It carries streams as well as rivers: the dense dendritic
-- network is the whole point of the map, so it must survive zooming out.
--
-- Generalisation is NOT done here. It is done per zoom at tile time, by
-- snapping to a one-pixel grid — see rivers/vector_layers.py. A fixed
-- tolerance would either lose detail at z8 or leave far too much at z2.
-- This only trims vertices finer than anything any cached zoom can resolve.
local TILE_SIMPLIFY_TOLERANCE = 50.0

-- Roles used by type=waterway relations, most significant first. A way can be a
-- member of several relations; the denormalised role on waterway_section keeps
-- the most significant one, while waterway_membership keeps them all.
local ROLE_PRIORITY = {
    main_stream = 1,
    side_stream = 2,
    anabranch = 3,
    tributary = 4,
    spring = 5,
}

-- Highest zoom that is cached, and so the highest worth expiring. Must stay in
-- step with CACHE_BELOW_ZOOM_LEVEL in rivers/tile_cache.py (this is that value
-- minus one). Each changed geometry emits one row per zoom, so raising it costs
-- almost nothing.
local MAX_CACHED_ZOOM = 10

local tables = {}

-- Tile expiry.
--
-- Flex only permits `expire` on Web Mercator geometry columns, which is why the
-- tile table below is 3857 while the canonical table stays 4326.
--
-- osm2pgsql rejects minzoom = 0 ("must be between 1 and maxzoom"). Zoom 0 is a
-- single tile that any change anywhere would expire, so the sync task evicts it
-- unconditionally instead — see rivers/tile_cache.py.
local expire_tiles = osm2pgsql.define_expire_output({
    minzoom = 1,
    maxzoom = MAX_CACHED_ZOOM,
    table = 'tile_expire',
})

-- Canonical table: the closest-river query, popups, and uncached high-zoom tiles.
-- 4326 because ClosestRiver casts to geography for true-metre distances.
tables.section = osm2pgsql.define_table({
    name = 'waterway_section',
    ids = { type = 'way', id_column = 'osm_way_id', create_index = 'primary_key' },
    columns = {
        { column = 'name', type = 'text' },
        -- Always 'river' or 'stream': the class the way is drawn and queried
        -- as, not necessarily its own tag. See PROMOTABLE_WATERWAY_TYPES; the
        -- literal tag is always in `tags`.
        { column = 'waterway', type = 'text', not_null = true },
        { column = 'role', type = 'text' },
        { column = 'river_osm_id', type = 'int8' },
        -- Arrays have no native flex type; sql_type is passed to PostgreSQL
        -- verbatim and the value must be a PostgreSQL array literal.
        { column = 'node_ids', sql_type = 'int8[]' },
        { column = 'tags', type = 'jsonb' },
        { column = 'geometry', type = 'linestring', projection = 4326, not_null = true },
    },
    indexes = {
        { column = 'geometry', method = 'gist' },
        { column = 'river_osm_id', method = 'btree' },
        { column = 'waterway', method = 'btree' },
        -- A rivers list is planned, so tag filtering stays indexed. Default GIN
        -- (not jsonb_path_ops) because it must support key-existence (?) as
        -- well as containment (@>).
        { column = 'tags', method = 'gin' },
        -- Reverse node -> way lookup, for FUTURE_FEATURES distance-along-river.
        { column = 'node_ids', method = 'gin' },
    },
})

tables.river = osm2pgsql.define_table({
    name = 'waterway_relation',
    ids = { type = 'relation', id_column = 'osm_id', create_index = 'primary_key' },
    columns = {
        { column = 'name', type = 'text' },
        { column = 'destination', type = 'text' },
        { column = 'wikipedia', type = 'text' },
        { column = 'tags', type = 'jsonb' },
    },
    indexes = {
        { column = 'tags', method = 'gin' },
    },
})

-- Source of truth for relation membership. One row per way member, so a way
-- belonging to several relations is represented faithfully. Keyed on the
-- relation id, so osm2pgsql removes a relation's rows when it is deleted.
tables.membership = osm2pgsql.define_table({
    name = 'waterway_membership',
    ids = { type = 'relation', id_column = 'river_osm_id' },
    columns = {
        { column = 'way_osm_id', type = 'int8', not_null = true },
        { column = 'role', type = 'text' },
        { column = 'sequence', type = 'int', not_null = true },
    },
    indexes = {
        { column = 'way_osm_id', method = 'btree' },
        -- The RiverMembership composite primary key, and the index that member
        -- order is read through. osm2pgsql cannot generate a surrogate key, so
        -- the model is keyed on this pair instead; unique by construction,
        -- because sequence is assigned per relation below.
        { column = { 'river_osm_id', 'sequence' }, method = 'btree', unique = true },
    },
})

tables.tile = osm2pgsql.define_table({
    name = 'waterway_tile',
    ids = { type = 'way', id_column = 'osm_way_id', create_index = 'primary_key' },
    columns = {
        { column = 'waterway', type = 'text', not_null = true },
        {
            column = 'geometry',
            type = 'linestring',
            projection = 3857,
            not_null = true,
            expire = expire_tiles,
        },
    },
    indexes = {
        { column = 'geometry', method = 'gist' },
        -- Every cached tile filters on this, so it must be indexed.
        { column = 'waterway', method = 'btree' },
    },
})

-- way id -> { river_osm_id = ..., role = ..., priority = ..., waterway = ... }
--
-- `waterway` is the class a promoted way is stored as, and is nil unless the way
-- belongs to a relation that is itself a river or stream. It is tracked
-- separately from the role contest below: a way in both a river relation and a
-- canal relation must still be promoted even when the canal relation wins on
-- role.
local way_to_river = {}

-- Tells osm2pgsql to re-process a relation's member ways when the relation
-- changes. Without this, adding a way to a relation would leave its
-- river_osm_id NULL forever. It also drives stage-2 processing on import.
function osm2pgsql.select_relation_members(relation)
    if relation.tags.type == 'waterway' then
        return { ways = osm2pgsql.way_member_ids(relation) }
    end
end

function osm2pgsql.process_relation(relation)
    if relation.tags.type ~= 'waterway' then
        return
    end

    tables.river:insert({
        name = relation.tags.name,
        destination = relation.tags.destination,
        wikipedia = relation.tags.wikipedia,
        tags = relation.tags,
    })

    -- nil unless the relation is a river or stream itself, which is what makes a
    -- promoted member's class well defined. Canal and drain relations (and the
    -- few with no waterway tag at all) promote nothing, but their river/stream
    -- members are still linked exactly as before.
    local class = WATERWAY_TYPES[relation.tags.waterway] and relation.tags.waterway
        or nil

    local sequence = 0
    for _, member in ipairs(relation.members) do
        if member.type == 'w' then
            sequence = sequence + 1

            tables.membership:insert({
                way_osm_id = member.ref,
                role = member.role ~= '' and member.role or nil,
                sequence = sequence,
            })

            local entry = way_to_river[member.ref]
            if not entry then
                entry = { priority = math.huge }
                way_to_river[member.ref] = entry
            end

            -- Keep the most significant role when a way is in several
            -- relations; unknown/empty roles rank last.
            local priority = ROLE_PRIORITY[member.role] or 99
            if priority < entry.priority then
                entry.river_osm_id = relation.id
                entry.role = member.role ~= '' and member.role or nil
                entry.priority = priority
            end

            -- River beats stream, so a promoted way draws as the heavier of the
            -- relations claiming it rather than by relation processing order.
            if class and entry.waterway ~= 'river' then
                entry.waterway = class
            end
        end
    end
end

function osm2pgsql.process_way(object)
    local waterway = object.tags.waterway
    local membership = way_to_river[object.id]

    -- The class of the river or stream relation this way belongs to, if any.
    -- way_to_river is still empty while stage-1 ways are read, so a way that is
    -- kept only because of this can only ever be inserted on the stage-2 pass
    -- that select_relation_members drives. That osm2pgsql reprocesses a way
    -- which inserted nothing at all in stage 1 is asserted by
    -- test_a_canal_in_a_river_relation_is_kept.
    local promoted = membership and membership.waterway

    if not WATERWAY_TYPES[waterway] then
        if not (promoted and PROMOTABLE_WATERWAY_TYPES[waterway]) then
            return
        end
        -- Draw and query it as the watercourse it is part of.
        waterway = promoted
    end

    -- Note there is deliberately no `osm2pgsql.stage == 1` guard here. Ways are
    -- inserted in both stages; osm2pgsql discards the stage-1 row for any way
    -- that gets re-processed in stage 2. Skipping stage 1 would drop every way
    -- that is not a relation member — which is most of them.
    local geom = object:as_linestring()
    if geom:is_null() then
        return
    end

    tables.section:insert({
        name = object.tags.name,
        waterway = waterway,
        role = membership and membership.role or nil,
        river_osm_id = membership and membership.river_osm_id or nil,
        node_ids = '{' .. table.concat(object.nodes, ',') .. '}',
        tags = object.tags,
        geometry = geom,
    })

    tables.tile:insert({
        waterway = waterway,
        geometry = geom:transform(3857):simplify(TILE_SIMPLIFY_TOLERANCE),
    })
end
