from django.core.files.storage import default_storage

from closest_river.rivers.models import OsmData
from closest_river.rivers.osm_ingestion import OsmParser
from config.celery_app import app


@app.task
def ingest_osm_data(osm_data_pk: int):
    data = OsmData.objects.get(pk=osm_data_pk)
    geo_fabrik_file = data.file
    parser = OsmParser()
    with default_storage.open(geo_fabrik_file.name, "rb") as file:
        parser.apply_buffer(file.read(), format="o5m", locations=True)
