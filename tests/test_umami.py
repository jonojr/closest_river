"""The optional Umami tracking tags in base.html.

What matters: nothing is loaded unless a website ID is configured, the scripts
come from wherever the settings say, the session replay recorder only loads
when separately switched on, and performance reporting can be switched off.
"""

from html.parser import HTMLParser

import pytest

pytestmark = pytest.mark.django_db

WEBSITE_ID = "94db1cb1-74f4-4a40-ad6c-962362670409"


class _ScriptTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.tags[attrs["src"]] = attrs


def _script_tags(client):
    """Each script on the home page by src, mapped to its attributes."""
    parser = _ScriptTags()
    parser.feed(client.get("/").content.decode())
    return parser.tags


def _tracked_scripts(client):
    """Each Umami script on the home page by src, mapped to its website ID."""
    return {
        src: attrs["data-website-id"]
        for src, attrs in _script_tags(client).items()
        if "data-website-id" in attrs
    }


def test_no_tracking_without_a_website_id(client, settings):
    settings.UMAMI_WEBSITE_ID = ""
    settings.UMAMI_SESSION_REPLAY = True

    assert _tracked_scripts(client) == {}


def test_the_tracker_loads_with_a_website_id(client, settings):
    settings.UMAMI_WEBSITE_ID = WEBSITE_ID
    settings.UMAMI_SESSION_REPLAY = False

    assert _tracked_scripts(client) == {"/script.js": WEBSITE_ID}


def test_the_recorder_loads_when_replays_are_on(client, settings):
    settings.UMAMI_WEBSITE_ID = WEBSITE_ID
    settings.UMAMI_SESSION_REPLAY = True

    assert _tracked_scripts(client) == {
        "/script.js": WEBSITE_ID,
        "/recorder.js": WEBSITE_ID,
    }


def test_the_scripts_can_come_from_another_host(client, settings):
    settings.UMAMI_WEBSITE_ID = WEBSITE_ID
    settings.UMAMI_SCRIPT_URL = "https://umami.example.com/stats.js"
    settings.UMAMI_RECORDER_URL = "https://umami.example.com/recorder.js"
    settings.UMAMI_SESSION_REPLAY = True

    assert _tracked_scripts(client) == {
        "https://umami.example.com/stats.js": WEBSITE_ID,
        "https://umami.example.com/recorder.js": WEBSITE_ID,
    }


@pytest.mark.parametrize(("enabled", "expected"), [(True, "true"), (False, None)])
def test_performance_reporting_can_be_switched_off(client, settings, enabled, expected):
    settings.UMAMI_WEBSITE_ID = WEBSITE_ID
    settings.UMAMI_PERFORMANCE = enabled

    tracker = _script_tags(client)["/script.js"]

    assert tracker.get("data-performance") == expected
