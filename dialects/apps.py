from django.apps import AppConfig


class DialectsConfig(AppConfig):
    name = "dialects"
    verbose_name = "Member meeting dialects"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import sfs  # noqa
        from . import main_or_subst_er  # noqa
        from . import main_subst_delegate  # noqa
        from . import skk_fum  # noqa
        from . import skr_agarrad  # noqa
