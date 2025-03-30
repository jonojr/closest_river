import logging
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from django.contrib.gis.db.models import LineStringField
from django.contrib.gis.db.models.aggregates import Union
from django.contrib.gis.db.models.functions import Length
from django.contrib.gis.geos import LineString
from django.contrib.gis.geos import MultiLineString
from django.contrib.gis.measure import Distance
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models

logger = logging.getLogger(__name__)


class RiverSection(models.Model):
    name = models.CharField(max_length=255)
    osm_way_id = models.CharField(max_length=1024, blank=True, default="")
    osm_node_ids = ArrayField(
        models.CharField(max_length=1024),
        default=list,
        blank=True,
    )
    tags = models.JSONField(default=dict, blank=True)
    geometry = LineStringField(blank=True, null=True)
    river = models.ForeignKey(
        "River",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="sections",
    )

    class Meta:
        indexes = [GinIndex("tags", name="river_section_tag_index")]

    def __str__(self):
        return f"{self.name} ({self.osm_way_id})" if self.name else str(self.osm_way_id)


class River(models.Model):
    osm_id = models.CharField(max_length=1024, blank=True, default="")
    name = models.CharField(max_length=1024)
    destination = models.CharField(max_length=1024, default="", blank=True)
    wikipedia = models.CharField(max_length=1024, default="", blank=True)
    tags = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.name} ({self.osm_id})" if self.name else str(self.osm_id)

    @property
    def geometry(self) -> LineString | MultiLineString | None:
        if self.sections.count() == 0:
            return None

        aggregate = self.sections.aggregate(Union("geometry"))

        return aggregate["geometry__union"]

    @property
    def length(self) -> Distance:
        if self.sections.count() == 0:
            return Distance()

        return self.sections.aggregate(total_length=Length(Union("geometry")))[
            "total_length"
        ]

    @property
    def elevations(self):
        steps_per_km = 0.33

        geometry = self.geometry

        if isinstance(geometry, MultiLineString):
            geometry = sorted(geometry, key=lambda x: x.length, reverse=True)[0]

        length_in_km = round(self.length.km)

        number_of_steps = int(max(length_in_km * steps_per_km, 30))

        points_to_check = [
            geometry.interpolate_normalized(step / number_of_steps)
            for step in range(number_of_steps + 1)
        ]

        request_data = {
            "locations": [
                {"latitude": point.coords[1], "longitude": point.coords[0]}
                for point in points_to_check
            ],
        }

        request = requests.post(
            url="http://10.0.0.253:5002/api/v1/lookup",
            json=request_data,
            timeout=30,
        )

        result = []
        for index, point in enumerate(request.json()["results"]):
            result.append([index / number_of_steps * length_in_km, point["elevation"]])
        return result


class FetchingOSMDataError(Exception):
    pass


# ruff: noqa: S603, S607
class OsmData(models.Model):
    file = models.FileField(upload_to="osm_data/")
    date_uploaded = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name if self.file else "Empty data"

    def download_osm_data(
        self,
        url: str = "https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf",
        filename: str = "planet-latest.osm.pbf",
    ):
        try:
            download_start = time.perf_counter()
            response = requests.get(url, timeout=1000)
            download_end = time.perf_counter()

            logger.info("Download time: %s", download_end - download_start)
            response.raise_for_status()
        except (requests.HTTPError, ValueError) as e:
            raise FetchingOSMDataError from e

        self.file.save(
            filename,
            ContentFile(response.content),
            save=True,
        )
        return self

    def convert_to_o5m_and_filter(self):
        with (
            default_storage.open(self.file.name, "rb") as file,
            tempfile.NamedTemporaryFile(delete=False) as temp_input_file,
        ):
            temp_input_file.write(file.read())
            temp_input_file_path = temp_input_file.name

        converted_filename = "planet-latest.osm.o5m"
        filtered_filename = "planet-latest-rivers.osm.o5m"

        logger.info("Converting file to o5m format.")
        convert_start = time.perf_counter()
        subprocess.run(
            [
                "osmconvert",
                temp_input_file_path,
                f"-o={converted_filename}",
            ],
            check=True,
        )
        convert_end = time.perf_counter()
        logger.info(
            "Finished converting file to o5m format. Runtime: %s",
            convert_end - convert_start,
        )

        logger.info("Filtering file to rivers only.")
        filter_start = time.perf_counter()
        subprocess.run(
            [
                "osmfilter",
                "australia-latest.o5m",
                '--keep="waterway=river"',
                f"-o={filtered_filename}",
            ],
            check=False,
        )
        filter_end = time.perf_counter()
        logger.info(
            "Finished filtering file to rivers only. Runtime: %s",
            filter_end - filter_start,
        )

        with Path(filtered_filename).open("rb") as result_file:
            self.file.save(
                filtered_filename,
                content=ContentFile(result_file.read()),
            )

        Path.unlink(Path(temp_input_file_path))
        Path.unlink(Path(converted_filename))
        Path.unlink(Path(filtered_filename))
