"""
Read-side queries for the timetable. Results are shaped for templates
(rows of cells, flat records), so templates never need dictionary lookups.
"""

from collections import defaultdict

from django.db.models import Count

from school.models import SubjectRequirement

from .models import TimetableEntry


def school_completion():
    """Lessons placed towards requirements, school-wide (extra lessons are not counted)."""
    placed = defaultdict(int)
    for row in TimetableEntry.objects.values("class_group_id", "subject_id").annotate(n=Count("id")):
        placed[(row["class_group_id"], row["subject_id"])] = row["n"]
    required = assigned = 0
    for class_id, subject_id, periods in SubjectRequirement.objects.values_list(
        "class_group_id", "subject_id", "periods_per_week"
    ):
        required += periods
        assigned += min(placed[(class_id, subject_id)], periods)
    percent = round(100 * assigned / required) if required else 0
    return {"required": required, "assigned": assigned, "percent": percent}
