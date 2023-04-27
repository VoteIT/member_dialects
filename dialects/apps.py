from django.apps import AppConfig


class DialectsConfig(AppConfig):
    name = "dialects"
    verbose_name = "Member meeting dialects"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import sfs
        from . import main_or_subst_er
        from . import skk_fum
        from . import skr_agarrad
