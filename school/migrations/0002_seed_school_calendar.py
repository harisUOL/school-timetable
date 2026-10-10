"""
Seed the school calendar: the settings singleton (9 periods, recess after 4)
and Monday–Saturday as operating days that inherit those defaults.

Safe to re-run: existing records are left untouched.
"""

from django.db import migrations

DEFAULT_OPERATING_DAYS = [0, 1, 2, 3, 4, 5]  # Monday–Saturday


def seed(apps, schema_editor):
    SchoolSettings = apps.get_model("school", "SchoolSettings")
    SchoolDayConfiguration = apps.get_model("school", "SchoolDayConfiguration")

    SchoolSettings.objects.get_or_create(
        pk=1, defaults={"default_periods_per_day": 9, "default_recess_after_period": 4}
    )
    for day in DEFAULT_OPERATING_DAYS:
        SchoolDayConfiguration.objects.get_or_create(day=day, defaults={"is_operating": True})


class Migration(migrations.Migration):
    dependencies = [("school", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
