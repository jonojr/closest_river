from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RiversConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "closest_river.rivers"
    verbose_name = _("Rivers")
