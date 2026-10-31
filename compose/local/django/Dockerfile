# define an alias for the specific python version used in this file.
FROM docker.io/python:3.12.15-slim-trixie AS python

# Python build stage
FROM python AS python-build-stage

ARG BUILD_ENVIRONMENT=local

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  python3-dev \
  # psycopg dependencies
  libpq-dev

# Requirements are installed here to ensure they will be cached.
COPY ./requirements .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --wheel-dir /usr/src/app/wheels  \
  -r ${BUILD_ENVIRONMENT}.txt


# Python 'run' stage
FROM python AS python-run-stage

ARG BUILD_ENVIRONMENT=local
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENVIRONMENT}

WORKDIR ${APP_HOME}


# devcontainer dependencies and utils
RUN apt-get update && apt-get install --no-install-recommends -y \
  sudo git bash-completion nano ssh

# Create devcontainer user and add it to sudoers
RUN groupadd --gid 1000 dev-user \
  && useradd --uid 1000 --gid dev-user --shell /bin/bash --create-home dev-user \
  && echo dev-user ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/dev-user \
  && chmod 0440 /etc/sudoers.d/dev-user


# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # psycopg dependencies
  libpq-dev  \
  wait-for-it \
  # postgis dependencies
  gdal-bin \
  # Translations dependencies
  gettext \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/

# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/* \
  && rm -rf /wheels/

COPY ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint
RUN chmod +x /entrypoint

COPY ./compose/local/django/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start


COPY ./compose/local/django/celery/worker/start /start-celeryworker
RUN sed -i 's/\r$//g' /start-celeryworker
RUN chmod +x /start-celeryworker

COPY ./compose/local/django/celery/beat/start /start-celerybeat
RUN sed -i 's/\r$//g' /start-celerybeat
RUN chmod +x /start-celerybeat

COPY ./compose/local/django/celery/flower/start /start-flower
RUN sed -i 's/\r$//g' /start-flower
RUN chmod +x /start-flower


# copy application code to WORKDIR
COPY . ${APP_HOME}

ENTRYPOINT ["/entrypoint"]


# OSM tooling stage
# -----------------------------------------------------------------------------
# Used only by the osmworker service. See the production Dockerfile for why this
# is a stage on top of the app image rather than a separate one.
#
# Debian trixie ships osm2pgsql 2.1.1: has define_expire_output and the
# properties table, but predates 2.3 — so no max_tiles_geometry /
# max_tiles_overall / diff_expire in waterways.lua.
FROM python-run-stage AS osm-tools

RUN apt-get update && apt-get install --no-install-recommends -y \
  osm2pgsql \
  osmium-tool \
  # Fetching extracts and replication diffs, and psql for maintenance SQL.
  ca-certificates \
  curl \
  postgresql-client \
  # osm2pgsql-replication imports osmium.WriteHandler, removed in pyosmium 4.x,
  # so it runs on Debian's python3 with Debian's modules rather than the
  # application interpreter. See the production Dockerfile for detail.
  python3-pyosmium \
  python3-psycopg2 \
  && rm -rf /var/lib/apt/lists/*

# pmtiles, for cutting the self-hosted basemap out of the published planet
# archives (rivers/basemap.py). A single static binary; not packaged by Debian.
ARG PMTILES_VERSION=1.31.2
ARG TARGETARCH
RUN case "${TARGETARCH:-amd64}" in \
    amd64) arch=x86_64 ;; \
    arm64) arch=arm64 ;; \
    *) echo "No pmtiles build for ${TARGETARCH}" >&2; exit 1 ;; \
  esac \
  && curl -fsSL "https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_${arch}.tar.gz" \
  | tar -xz -C /usr/local/bin pmtiles \
  && pmtiles version

# Smoke-test the tooling. `osm2pgsql-replication --help` exits 1 by design, so
# assert on the imports it needs instead.
RUN osm2pgsql --version \
  && osmium --version \
  && /usr/bin/python3 -c "\
from osmium.replication.server import ReplicationServer; \
from osmium import WriteHandler; \
import psycopg2"

COPY ./compose/local/django/celery/osmworker/start /start-osmworker
RUN sed -i 's/\r$//g' /start-osmworker
RUN chmod +x /start-osmworker

ENTRYPOINT ["/entrypoint"]
