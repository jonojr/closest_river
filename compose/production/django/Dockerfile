FROM docker.io/node:20-bookworm-slim AS client-builder

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

COPY ./package.json ${APP_HOME}
RUN npm install && npm cache clean --force
COPY . ${APP_HOME}
# Baked into the map bundles at build time. .envs/ is in .dockerignore, so this
# is the only way the key reaches the build; without it the maps load with no
# basemap, so refuse to build rather than ship that. The key is public anyway
# (it is in the JS every visitor downloads), and this stage is not in the final
# image.
ARG MAPTILER_API_KEY
RUN test -n "${MAPTILER_API_KEY}" \
  || { echo "MAPTILER_API_KEY build arg is not set; see docker-compose.production.yml" >&2; exit 1; }
RUN npm run build
# define an alias for the specific python version used in this file.
FROM docker.io/python:3.12.13-slim-trixie AS python

# Python build stage
FROM python AS python-build-stage

ARG BUILD_ENVIRONMENT=production

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg dependencies
  libpq-dev


# Requirements are installed here to ensure they will be cached.
COPY ./requirements .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --wheel-dir /usr/src/app/wheels  \
  -r ${BUILD_ENVIRONMENT}.txt


# Python 'run' stage
FROM python AS python-run-stage

ARG BUILD_ENVIRONMENT=production
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENVIRONMENT}

WORKDIR ${APP_HOME}

# Pinned to 1000:1000 so the bind-mounted host directories (the osmworker's
# /data, media) can be chowned once and stay writable across rebuilds;
# --system alone picks whatever id is free at build time. useradd warns that
# 1000 is above SYS_UID_MAX, which is harmless.
RUN addgroup --system --gid 1000 django \
    && adduser --system --uid 1000 --ingroup django django


# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # psycopg dependencies
  libpq-dev \
  # postgis dependencies
  gdal-bin \
  # Translations dependencies
  gettext \
  # entrypoint
  wait-for-it \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/

# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/* \
  && rm -rf /wheels/


COPY --chown=django:django ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint
RUN chmod +x /entrypoint


COPY --chown=django:django ./compose/production/django/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start
COPY --chown=django:django ./compose/production/django/celery/worker/start /start-celeryworker
RUN sed -i 's/\r$//g' /start-celeryworker
RUN chmod +x /start-celeryworker


COPY --chown=django:django ./compose/production/django/celery/beat/start /start-celerybeat
RUN sed -i 's/\r$//g' /start-celerybeat
RUN chmod +x /start-celerybeat


COPY --chown=django:django ./compose/production/django/celery/flower/start /start-flower
RUN sed -i 's/\r$//g' /start-flower
RUN chmod +x /start-flower


# copy application code to WORKDIR
COPY --from=client-builder --chown=django:django ${APP_HOME} ${APP_HOME}


# make django owner of the WORKDIR directory as well.
RUN chown -R django:django ${APP_HOME}

USER django

RUN DATABASE_URL="" \
  DJANGO_SETTINGS_MODULE="config.settings.test" \
  python manage.py compilemessages

ENTRYPOINT ["/entrypoint"]


# OSM tooling stage
# -----------------------------------------------------------------------------
# Used only by the osmworker service. Kept as a stage on top of the app image so
# the application code and Python dependencies cannot drift from the web image,
# while osm2pgsql and osmium stay out of anything serving requests.
#
# Debian trixie ships osm2pgsql 2.1.1, which has define_expire_output (needed for
# tile expiry) and the properties table (needed by osm2pgsql-replication).
# It predates 2.3, so do NOT use max_tiles_geometry/max_tiles_overall/diff_expire
# in waterways.lua.
FROM python-run-stage AS osm-tools

USER root

RUN apt-get update && apt-get install --no-install-recommends -y \
  osm2pgsql \
  osmium-tool \
  # Fetching extracts and replication diffs, and psql for maintenance SQL.
  ca-certificates \
  curl \
  postgresql-client \
  # osm2pgsql-replication is a Debian script with a /usr/bin/python3 shebang.
  # It imports osmium.WriteHandler, which was REMOVED in pyosmium 4.x, so it
  # cannot run under the application interpreter (requirements pin
  # osmium==4.2.0). Give it Debian's own pyosmium 3.x and psycopg2 instead.
  python3-pyosmium \
  python3-psycopg2 \
  && rm -rf /var/lib/apt/lists/*

# Smoke-test the tooling at build time.
# Note: `osm2pgsql-replication --help` exits 1 by design — the script catches
# argparse's SystemExit and returns 1 — so assert on the imports it needs.
RUN osm2pgsql --version \
  && osmium --version \
  && /usr/bin/python3 -c "\
from osmium.replication.server import ReplicationServer; \
from osmium import WriteHandler; \
import psycopg2"

COPY --chown=django:django ./compose/production/django/celery/osmworker/start /start-osmworker
RUN sed -i 's/\r$//g' /start-osmworker
RUN chmod +x /start-osmworker

USER django

ENTRYPOINT ["/entrypoint"]
