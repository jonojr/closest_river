from django.conf import settings


def umami(request):
    """Expose the Umami tracking settings to templates."""
    return {
        "UMAMI_WEBSITE_ID": settings.UMAMI_WEBSITE_ID,
        "UMAMI_SCRIPT_URL": settings.UMAMI_SCRIPT_URL,
        "UMAMI_RECORDER_URL": settings.UMAMI_RECORDER_URL,
        "UMAMI_SESSION_REPLAY": settings.UMAMI_SESSION_REPLAY,
    }
