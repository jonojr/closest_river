"""Pipeline runs are kept in the database and browsable in the admin.

SyncState only remembers the last sync; task results are the history: what
each run returned, or the traceback it failed with.
"""

from http import HTTPStatus

import pytest
from celery.beat import Scheduler
from django.urls import reverse
from django_celery_results.models import TaskResult

from config.celery_app import app

pytestmark = pytest.mark.django_db


def test_a_sync_run_shows_in_the_admin(admin_client):
    TaskResult.objects.create(
        task_id="sync-1",
        task_name="closest_river.rivers.tasks.sync_osm_waterways",
        status="SUCCESS",
        result='{"tiles_expired": 12762}',
    )

    response = admin_client.get(
        reverse("admin:django_celery_results_taskresult_changelist"),
    )

    assert response.status_code == HTTPStatus.OK
    assert "closest_river.rivers.tasks.sync_osm_waterways" in response.text


def test_old_results_are_cleaned_up_by_beat():
    """Without the cleanup task, results would accumulate forever.

    The database backend cannot expire rows itself, so Beat schedules
    celery.backend_cleanup whenever result_expires is set. Decided in Celery's
    base scheduler, which django-celery-beat's DatabaseScheduler inherits;
    tested there because that one closes the test's database connection.
    """
    scheduler = Scheduler(app=app, lazy=True)

    scheduler.install_default_entries(scheduler.schedule)

    assert "celery.backend_cleanup" in scheduler.schedule
