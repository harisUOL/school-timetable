"""Sidebar navigation. An item is disabled until its page exists (its URL name resolves)."""

from django.urls import NoReverseMatch, reverse

from school.roles import EDITOR_GROUP, VIEWER_GROUP

SIDEBAR = [
    ("Dashboard", "scheduling:dashboard"),
    ("Class Timetables", "scheduling:timetable"),
    ("Teachers", "scheduling:teachers"),
    ("Teacher Workloads", "scheduling:workloads"),
    ("Subjects", "scheduling:subjects"),
    ("Subject Requirements", "scheduling:requirements"),
    ("Availability", "school:availability"),
    ("Data Health", "school:health"),
    ("Generator", "generator:index"),
    ("Settings", "school:settings"),
]


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def navigation(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    current = f"{match.namespace}:{match.url_name}" if match and match.namespace else ""
    items = []
    for label, name in SIDEBAR:
        url = _url(name)
        items.append(
            {
                "label": label,
                "url": url,
                "enabled": url is not None,
                "active": name == current or name in _ACTIVE_ALIASES.get(current, ()),
            }
        )
    return {"sidebar_items": items, "role_label": role_label(request.user)}


# Pages that highlight a sidebar item other than their own.
_ACTIVE_ALIASES = {
    "scheduling:class_timetable": ("scheduling:timetable",),
    "scheduling:reset_class": ("scheduling:timetable",),
    "scheduling:teacher_timetable": ("scheduling:teachers",),
    "scheduling:conflicts": ("scheduling:requirements",),
    "generator:run_detail": ("generator:index",),
}


def role_label(user):
    if user.is_superuser:
        return "Administrator"
    names = set(user.groups.values_list("name", flat=True))
    for group in (EDITOR_GROUP, VIEWER_GROUP):
        if group in names:
            return group
    return "No role assigned"
