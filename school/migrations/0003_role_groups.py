"""
Create the "Timetable Editor" and "Viewer" groups with their permissions.

Permissions are normally created after migrations finish, so they are created
here first. Re-running only adds missing permissions; nothing is removed.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

from school.roles import EDITOR_GROUP, VIEWER_GROUP, sync_role_groups


def create_groups(apps, schema_editor):
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    sync_role_groups(apps.get_model("auth", "Group"), apps.get_model("auth", "Permission"))


def remove_groups(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=[EDITOR_GROUP, VIEWER_GROUP]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("school", "0002_seed_school_calendar"),
        ("scheduling", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(create_groups, remove_groups)]
