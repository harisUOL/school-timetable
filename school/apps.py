from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SchoolConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "school"
    verbose_name = "School configuration"

    def ready(self):
        from .roles import sync_role_groups_after_migrate

        # Keeps role groups in step with models added by later migrations.
        post_migrate.connect(sync_role_groups_after_migrate, dispatch_uid="school_sync_roles")
