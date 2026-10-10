"""
Role groups and the permissions each one holds.

- Timetable Editor: reads everything, builds timetables, edits teacher capacities,
  runs the generator.
- Viewer: reads everything and can export; cannot change anything.
- Superusers have every permission and are the only ones who reach the admin
  and the school settings.

`sync_role_groups` is additive: it creates the groups and grants the
permissions listed here, but never removes a permission an administrator
added by hand. It runs from a data migration and after every `migrate`, so
models added in later phases are picked up automatically.
"""

EDITOR_GROUP = "Timetable Editor"
VIEWER_GROUP = "Viewer"

# Apps whose models every role may view.
VIEWABLE_APPS = ["school", "scheduling", "generator"]

EDITOR_EXTRA_PERMISSIONS = [
    "scheduling.add_timetableentry",
    "scheduling.change_timetableentry",
    "scheduling.delete_timetableentry",
    "school.add_teacherdailyavailability",
    "school.change_teacherdailyavailability",
    "school.delete_teacherdailyavailability",
    "generator.add_generationrun",
    "generator.change_generationrun",
]

# The permission that every timetable write requires (CLAUDE.md).
TIMETABLE_WRITE_PERMISSION = "scheduling.change_timetableentry"
CAPACITY_WRITE_PERMISSION = "school.change_teacherdailyavailability"
GENERATOR_RUN_PERMISSION = "generator.add_generationrun"


def sync_role_groups(Group, Permission):
    """Create the groups and grant their permissions (model classes passed in for migrations)."""
    view_perms = Permission.objects.filter(
        content_type__app_label__in=VIEWABLE_APPS, codename__startswith="view_"
    )
    extra = Permission.objects.none()
    for dotted in EDITOR_EXTRA_PERMISSIONS:
        app_label, codename = dotted.split(".")
        extra = extra | Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        )

    viewer, _ = Group.objects.get_or_create(name=VIEWER_GROUP)
    viewer.permissions.add(*view_perms)

    editor, _ = Group.objects.get_or_create(name=EDITOR_GROUP)
    editor.permissions.add(*view_perms, *extra)


def sync_role_groups_after_migrate(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    sync_role_groups(Group, Permission)
