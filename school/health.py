"""
Data health: configuration problems that block or weaken scheduling.

`run_health_checks()` returns a flat list of `Issue`s, errors first. Each
issue names the people/classes involved and links to the admin page where it
can be fixed. Errors make a complete timetable impossible (the generator
refuses to run while any exist); warnings are worth a look but do not block.

All checks read configuration only. They never look at timetable entries:
that is the conflict report's job (Phase 4).
"""

from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

from .constants import Day
from .day_schedule import get_week_schedule
from .models import SubjectRequirement, Teacher, TeacherDailyAvailability

ERROR = "error"
WARNING = "warning"

# code -> (group title shown on the Data Health page, severity)
CHECKS = {
    "NO_QUALIFIED_TEACHER": ("Requirements with no qualified active teacher", ERROR),
    "DESIGNATED_UNQUALIFIED": ("Designated teacher is not qualified", ERROR),
    "DESIGNATED_INACTIVE": ("Designated teacher is inactive", ERROR),
    "CLASS_OVER_SLOTS": ("Classes needing more periods than the week has", ERROR),
    "TEACHER_OVERLOADED": ("Designated load above weekly capacity", ERROR),
    "CLASS_LOAD_UNFITTABLE": ("Designated lessons on a class cannot fit daily capacities", WARNING),
    "SUBJECT_UNDERSUPPLIED": ("Subjects with more demand than qualified capacity", ERROR),
    "NO_CAPACITY_RECORDS": ("Active teachers with no capacity records", WARNING),
    "CAPACITY_ABOVE_DAY_LENGTH": ("Capacity records above the day's length", WARNING),
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    fix_url: str = ""
    fix_label: str = "Fix in admin"

    @property
    def severity(self):
        return CHECKS[self.code][1]

    @property
    def title(self):
        return CHECKS[self.code][0]

    @property
    def is_error(self):
        return self.severity == ERROR


@dataclass(frozen=True)
class IssueGroup:
    code: str
    title: str
    severity: str
    issues: list


def admin_change(obj):
    meta = obj._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])


class _Snapshot:
    """Everything the checks need, loaded in a handful of queries."""

    def __init__(self):
        self.week = {s.day: s for s in get_week_schedule()}
        self.weekly_slots = sum(s.periods for s in self.week.values())
        self.teachers = {t.pk: t for t in Teacher.objects.all()}
        self.qualified = defaultdict(set)  # subject_id -> {teacher_id}
        for teacher_id, subject_id in Teacher.subjects.through.objects.values_list(
            "teacher_id", "subject_id"
        ):
            self.qualified[subject_id].add(teacher_id)
        self.capacity_records = list(TeacherDailyAvailability.objects.select_related("teacher"))
        self.capacity = defaultdict(dict)  # teacher_id -> {day: usable max}
        for record in self.capacity_records:
            schedule = self.week.get(record.day)
            if schedule:
                self.capacity[record.teacher_id][record.day] = min(
                    record.max_periods, schedule.periods
                )
        self.requirements = list(
            SubjectRequirement.objects.select_related(
                "class_group__grade", "class_group__section", "subject", "teacher"
            )
        )

    def weekly_capacity(self, teacher_id):
        return sum(self.capacity[teacher_id].values())

    def active_qualified(self, subject_id):
        return [
            tid for tid in self.qualified[subject_id] if self.teachers[tid].is_active
        ]


def run_health_checks():
    snap = _Snapshot()
    issues = []
    for check in (
        _requirements_without_teacher,
        _designated_teacher_problems,
        _classes_over_slots,
        _teacher_overloaded,
        _class_load_unfittable,
        _subject_undersupplied,
        _teachers_without_capacity,
        _capacity_above_day_length,
    ):
        issues.extend(check(snap))
    order = list(CHECKS)
    return sorted(issues, key=lambda i: (not i.is_error, order.index(i.code)))


def group_issues(issues):
    groups = {}
    for issue in issues:
        group = groups.get(issue.code)
        if group is None:
            group = groups[issue.code] = IssueGroup(
                issue.code, issue.title, issue.severity, []
            )
        group.issues.append(issue)
    return list(groups.values())


def has_errors(issues=None):
    return any(i.is_error for i in (run_health_checks() if issues is None else issues))


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def _requirements_without_teacher(snap):
    for req in snap.requirements:
        if not snap.active_qualified(req.subject_id):
            yield Issue(
                "NO_QUALIFIED_TEACHER",
                f"{req.class_group} needs {req.subject} but no active teacher is qualified "
                "to teach it.",
                reverse("admin:school_teacher_changelist"),
                "Qualify a teacher",
            )


def _designated_teacher_problems(snap):
    for req in snap.requirements:
        teacher = req.teacher
        if teacher is None:
            continue
        if not teacher.is_active:
            yield Issue(
                "DESIGNATED_INACTIVE",
                f"{teacher} is designated for {req.class_group} {req.subject} but is inactive.",
                admin_change(req),
                "Change requirement",
            )
        elif teacher.pk not in snap.qualified[req.subject_id]:
            yield Issue(
                "DESIGNATED_UNQUALIFIED",
                f"{teacher} is designated for {req.class_group} {req.subject} but is not "
                f"qualified to teach {req.subject}.",
                admin_change(req),
                "Change requirement",
            )


def _classes_over_slots(snap):
    totals = defaultdict(int)
    classes = {}
    for req in snap.requirements:
        totals[req.class_group_id] += req.periods_per_week
        classes[req.class_group_id] = req.class_group
    for class_id, total in totals.items():
        if total > snap.weekly_slots:
            yield Issue(
                "CLASS_OVER_SLOTS",
                f"{classes[class_id]} requires {total} periods a week but has only "
                f"{snap.weekly_slots} teaching slots.",
                admin_change(classes[class_id]),
                "Edit class requirements",
            )


def _active_designations(snap):
    for req in snap.requirements:
        if req.teacher_id and req.teacher.is_active:
            yield req


def _teacher_overloaded(snap):
    load = defaultdict(int)
    for req in _active_designations(snap):
        load[req.teacher_id] += req.periods_per_week
    for teacher_id, designated in load.items():
        capacity = snap.weekly_capacity(teacher_id)
        if designated > capacity:
            teacher = snap.teachers[teacher_id]
            yield Issue(
                "TEACHER_OVERLOADED",
                f"{teacher} is designated for {designated} lessons a week but can teach "
                f"only {capacity}.",
                admin_change(teacher),
                "Edit teacher",
            )


def _class_load_unfittable(snap):
    """
    Can a teacher's designated lessons with one class be spread over the days
    they are available? On each day they can give that class at most
    min(their capacity, the day's periods, the per-subject spread limit × the
    number of subjects they teach it). Exceeding this forces several lessons
    of one subject into a single day, so it is a warning, not an error.
    """
    per_class = defaultdict(lambda: [0, 0])  # (teacher, class) -> [lessons, subjects]
    labels = {}
    for req in _active_designations(snap):
        entry = per_class[(req.teacher_id, req.class_group_id)]
        entry[0] += req.periods_per_week
        entry[1] += 1
        labels[req.class_group_id] = req.class_group
    spread = settings.TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY
    for (teacher_id, class_id), (designated, subject_count) in per_class.items():
        fit = sum(min(cap, spread * subject_count) for cap in snap.capacity[teacher_id].values())
        if designated > fit:
            teacher = snap.teachers[teacher_id]
            days = ", ".join(
                f"{Day(day).label} {cap}" for day, cap in sorted(snap.capacity[teacher_id].items())
            )
            yield Issue(
                "CLASS_LOAD_UNFITTABLE",
                f"{teacher} is designated for {designated} lessons a week with "
                f"{labels[class_id]}, but their daily capacities ({days or 'none'}) allow "
                f"only {fit} without more than {spread} lessons of a subject in one day.",
                admin_change(teacher),
                "Edit teacher",
            )


def _subject_undersupplied(snap):
    demand = defaultdict(int)
    subjects = {}
    for req in snap.requirements:
        demand[req.subject_id] += req.periods_per_week
        subjects[req.subject_id] = req.subject
    for subject_id, needed in demand.items():
        teachers = snap.active_qualified(subject_id)
        if not teachers:
            continue  # Already reported per requirement.
        supply = sum(snap.weekly_capacity(tid) for tid in teachers)
        if needed > supply:
            yield Issue(
                "SUBJECT_UNDERSUPPLIED",
                f"{subjects[subject_id]} needs {needed} lessons a week across the school, "
                f"but its {len(teachers)} qualified teacher(s) can teach at most {supply} "
                "in total.",
                reverse("admin:school_subject_change", args=[subject_id]),
                "View subject",
            )


def _teachers_without_capacity(snap):
    configured = {record.teacher_id for record in snap.capacity_records}
    for teacher in snap.teachers.values():
        if teacher.is_active and teacher.pk not in configured:
            yield Issue(
                "NO_CAPACITY_RECORDS",
                f"{teacher} has no daily capacity records, so they cannot be scheduled "
                "on any day.",
                admin_change(teacher),
                "Set capacities",
            )


def _capacity_above_day_length(snap):
    for record in snap.capacity_records:
        schedule = snap.week.get(record.day)
        length = schedule.periods if schedule else 0
        if record.max_periods > length:
            where = (
                f"{schedule.label} has only {length} periods"
                if schedule
                else f"the school is closed on {Day(record.day).label}"
            )
            yield Issue(
                "CAPACITY_ABOVE_DAY_LENGTH",
                f"{record.teacher} may teach {record.max_periods} lessons on "
                f"{Day(record.day).label}, but {where}.",
                admin_change(record),
                "Edit capacity",
            )

