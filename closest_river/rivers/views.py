import gzip
import re

from django.http import HttpResponse
from django.utils.cache import patch_vary_headers
from django.views.generic import DetailView
from vectortiles.views import MVTView
from vectortiles.views import TileJSONView

from closest_river.rivers.models import River
from closest_river.rivers.tile_cache import CACHE_BELOW_ZOOM_LEVEL
from closest_river.rivers.tile_cache import cached_tile
from closest_river.rivers.vector_layers import RiverSectionVectorLayer
from closest_river.rivers.vector_layers import StreamSectionVectorLayer

# As GZipMiddleware tests it.
_ACCEPTS_GZIP = re.compile(r"\bgzip\b")


class RiverSectionBaseLayer:
    # Streams first so rivers are drawn over the wash by default.
    layer_classes = [StreamSectionVectorLayer, RiverSectionVectorLayer]


class RiverSectionTileView(RiverSectionBaseLayer, MVTView):
    def get(self, request, z, x, y, *args, **kwargs):
        """Serve the low zooms straight from the cache, already gzipped.

        GZipMiddleware leaves a response alone once it has a Content-Encoding,
        so these are compressed once, when cached, not on every request. Above
        the cache line, tiles are rendered per request and the middleware
        compresses them as before.
        """
        z, x, y = int(z), int(x), int(y)
        if z >= CACHE_BELOW_ZOOM_LEVEL:
            return super().get(request, z, x, y, *args, **kwargs)

        tile = cached_tile(self.get_layers(), z, x, y)
        if not tile:
            return HttpResponse(status=204, content_type=self.content_type)

        if _ACCEPTS_GZIP.search(request.headers.get("Accept-Encoding", "")):
            response = HttpResponse(tile, content_type=self.content_type)
            response.headers["Content-Encoding"] = "gzip"
        else:
            # Every browser asks for gzip; this is for curl and the odd bot.
            response = HttpResponse(
                gzip.decompress(tile),
                content_type=self.content_type,
            )
        patch_vary_headers(response, ("Accept-Encoding",))
        return response


class RiverSectionTileJSON(RiverSectionBaseLayer, TileJSONView):
    name = "All rivers dataset"
    attribution = "OSM"
    description = "All ways of type river from OSM"

    tile_url = "/rivers/river-sections/tile/{x}/{y}/{z}"


class RiverDetailView(DetailView):
    model = River
    template_name = "pages/river_detail.html"
    context_object_name = "river"
    slug_field = "osm_id"
    slug_url_kwarg = "osm_id"
