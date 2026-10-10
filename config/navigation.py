"""
Sidebar navigation.

Each item names the permission needed to open it: items the user cannot open
are hidden, and an item whose page does not exist yet (its URL name does not
resolve) is shown disabled.
"""

from django.urls import NoReverseMatch, reverse

from school.roles import EDITOR_GROUP, VIEWER_GROUP

READ = "scheduling.view_timetableentry"
SUPERUSER = "superuser"

SIDEBAR = [
    ("Dashboard", "scheduling:dashboard", READ),
    ("Class Timetables", "scheduling:timetable", READ),
    ("Teachers", "scheduling:teachers", READ),
    ("Teacher Workloads", "scheduling:workloads", READ),
    ("Subjects", "scheduling:subjects", READ),
    ("Subject Requirements", "scheduling:requirements", READ),
    ("Availability", "school:availability", "school.view_teacherdailyavailability"),
    ("Data Health", "school:health", "school.view_subjectrequirement"),
    ("Generator", "generator:index", "generator.view_generationrun"),
    ("Settings", "school:settings", SUPERUSER),
]

# Pages that highlight a sidebar item other than their own.
_ACTIVE_ALIASES = {
    "scheduling:class_timetable": ("scheduling:timetable",),
    "scheduling:reset_class": ("scheduling:timetable",),
    "scheduling:teacher_timetable": ("scheduling:teachers",),
    "scheduling:conflicts": ("scheduling:requirements",),
    "generator:run_detail": ("generator:index",),
}


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _allowed(user, permission):
    return user.is_superuser if permission == SUPERUSER else user.has_perm(permission)


def navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    current = f"{match.namespace}:{match.url_name}" if match and match.namespace else ""
    items = []
    for item in SIDEBAR:
        label, name, permission = item
        if not _allowed(user, permission):
            continue
        url = _url(name)
        items.append(
            {
                "label": label,
                "url": url,
                "enabled": url is not None,
                "active": name == current or name in _ACTIVE_ALIASES.get(current, ()),
            }
        )
    return {"sidebar_items": items, "role_label": role_label(user)}


def role_label(user):
    if user.is_superuser:
        return "Administrator"
    names = set(user.groups.values_list("name", flat=True))
    for group in (EDITOR_GROUP, VIEWER_GROUP):
        if group in names:
            return group
    return "No role assigned"
