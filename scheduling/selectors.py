"""
Read-side queries for the timetable. Results are shaped for templates
(rows of cells, flat records), so templates never need dictionary lookups.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Count

from school.constants import Day
from school.day_schedule import get_week_schedule
from school.models import SubjectRequirement, Teacher, TeacherDailyAvailability

from . import validators as v
from .models import TimetableEntry


def day_label(day):
    return Day(day).label


ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman(number):
    result = ""
    for value, numeral in ROMAN:
        while number >= value:
            result += numeral
            number -= value
    return result


# --------------------------------------------------------------------------
# Class grid
# --------------------------------------------------------------------------

LESSON, EMPTY, RECESS, NONE = "lesson", "empty", "recess", "none"


@dataclass
class Cell:
    kind: str  # lesson | empty | recess | none
    day: int = None
    day_label: str = ""
    period: int = None
    entry: TimetableEntry = None

    @property
    def roman(self):
        return roman(self.period) if self.period else ""

    @property
    def is_slot(self):
        return self.kind in (LESSON, EMPTY)

    @property
    def updated_at(self):
        return self.entry.updated_at.isoformat() if self.entry else ""

    @property
    def aria_label(self):
        where = f"{self.day_label}, period {self.period}"
        if self.entry:
            locked = ", locked" if self.entry.is_locked else ""
            return f"{where}: {self.entry.subject.name} with {self.entry.teacher.name}{locked}"
        return f"{where}: empty"


@dataclass
class Row:
    day: int
    label: str
    short_label: str
    cells: list


@dataclass
class Grid:
    columns: list  # header cells: {"kind": "period"|"recess", "label": ...}
    rows: list
    filled: int
    total_slots: int


def row_cells(schedule, width, recess_after, entries_by_period):
    """
    One row of the grid: periods 1..width, with recess inserted after this
    day's recess position. Periods past the day's end are "no lesson" cells.
    A day without recess gets a filler cell so every row has the same width.
    """
    cells = []
    for period in range(1, width + 1):
        if period <= schedule.periods:
            entry = entries_by_period.get(period)
            cells.append(
                Cell(LESSON if entry else EMPTY, schedule.day, schedule.label, period, entry)
            )
        else:
            cells.append(Cell(NONE, schedule.day, schedule.label, period))
        if schedule.has_recess and period == schedule.recess_after:
            cells.append(Cell(RECESS, schedule.day, schedule.label))
    if recess_after and not schedule.has_recess:
        cells.append(Cell(NONE, schedule.day, schedule.label))
    return cells


def grid_header(week):
    width = max((s.periods for s in week), default=0)
    # The recess column follows the first longest day that has a recess.
    with_recess = sorted((s for s in week if s.has_recess), key=lambda s: -s.periods)
    recess_after = with_recess[0].recess_after if with_recess else 0
    columns = []
    for period in range(1, width + 1):
        columns.append({"kind": "period", "label": roman(period), "period": period})
        if recess_after and period == recess_after:
            columns.append({"kind": "recess", "label": "Recess"})
    return width, recess_after, columns


def class_grid(class_group):
    week = get_week_schedule()
    width, recess_after, columns = grid_header(week)
    entries = defaultdict(dict)
    for entry in TimetableEntry.objects.with_related().filter(class_group=class_group):
        entries[entry.day][entry.period] = entry
    rows = [
        Row(s.day, s.label, s.short_label, row_cells(s, width, recess_after, entries[s.day]))
        for s in week
    ]
    filled = sum(1 for row in rows for cell in row.cells if cell.kind == LESSON)
    return Grid(columns, rows, filled, sum(s.periods for s in week))


def class_cell(class_group, day, period):
    """A single slot cell, for re-rendering after a change."""
    entry = TimetableEntry.objects.with_related().filter(
        class_group=class_group, day=day, period=period
    ).first()
    schedule = next(s for s in get_week_schedule() if s.day == day)
    return Cell(LESSON if entry else EMPTY, day, schedule.label, period, entry)


# --------------------------------------------------------------------------
# Requirement progress
# --------------------------------------------------------------------------


@dataclass
class SubjectProgress:
    subject_id: int
    subject: str
    code: str
    required: int
    assigned: int
    designated: str = ""
    teachers: list = field(default_factory=list)  # names actually teaching it

    @property
    def remaining(self):
        return max(self.required - self.assigned, 0)

    @property
    def percent(self):
        return min(round(100 * self.assigned / self.required), 100) if self.required else 0

    @property
    def is_met(self):
        return self.assigned >= self.required

    @property
    def is_over(self):
        return self.assigned > self.required

    @property
    def designated_differs(self):
        return bool(self.designated) and any(name != self.designated for name in self.teachers)


@dataclass
class Progress:
    subjects: list
    required: int
    assigned: int
    weekly_slots: int

    @property
    def remaining(self):
        return max(self.required - self.assigned, 0)

    @property
    def percent(self):
        return min(round(100 * self.assigned / self.required), 100) if self.required else 0

    @property
    def over_capacity(self):
        return self.required > self.weekly_slots


def requirement_progress(class_group):
    placed = defaultdict(list)
    for subject_id, teacher_name in TimetableEntry.objects.filter(
        class_group=class_group
    ).values_list("subject_id", "teacher__name"):
        placed[subject_id].append(teacher_name)
    subjects = [
        SubjectProgress(
            subject_id=req.subject_id,
            subject=req.subject.name,
            code=req.subject.code or "",
            required=req.periods_per_week,
            assigned=len(placed[req.subject_id]),
            designated=req.teacher.name if req.teacher else "",
            teachers=sorted(set(placed[req.subject_id])),
        )
        for req in SubjectRequirement.objects.filter(class_group=class_group)
        .select_related("subject", "teacher")
        .order_by("subject__name")
    ]
    return Progress(
        subjects=subjects,
        required=sum(s.required for s in subjects),
        assigned=sum(min(s.assigned, s.required) for s in subjects),
        weekly_slots=sum(s.periods for s in get_week_schedule()),
    )


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


# --------------------------------------------------------------------------
# Teacher load
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Load:
    assigned: int
    max: int | None  # None: no capacity record (unavailable)

    @property
    def label(self):
        return f"{self.assigned}/{self.max if self.max is not None else 0}"


def teacher_day_load(teacher_ids, day):
    """{teacher_id: Load} for one day, counting lessons across ALL classes."""
    teacher_ids = list(teacher_ids)
    counts = dict(
        TimetableEntry.objects.filter(teacher_id__in=teacher_ids, day=day)
        .values_list("teacher_id")
        .annotate(n=Count("id"))
    )
    caps = dict(
        TeacherDailyAvailability.objects.filter(teacher_id__in=teacher_ids, day=day).values_list(
            "teacher_id", "max_periods"
        )
    )
    return {tid: Load(counts.get(tid, 0), caps.get(tid)) for tid in teacher_ids}


# --------------------------------------------------------------------------
# Slot options (the assignment modal)
# --------------------------------------------------------------------------


def slot_options(class_group, day, period):
    """
    Everything the modal needs for one slot, as JSON-ready data.

    Subjects are the class's requirements with progress. For each subject the
    qualified active teachers are listed with their status (available, or
    blocked with the reason), day load and weekly load. Unqualified teachers
    are never listed. If the slot holds a lesson, it is excluded from every
    count, exactly as a replacement would be validated.
    """
    state = v.MemoryState.from_database()
    schedule = state.day_schedule(day)
    if schedule is None or not schedule.is_valid_period(period):
        raise ValueError(f"Not a teaching slot: day {day}, period {period}")

    entry = (
        TimetableEntry.objects.with_related()
        .filter(class_group=class_group, day=day, period=period)
        .first()
    )
    exclude = entry.pk if entry else None

    teachers = {t.pk: t for t in Teacher.objects.filter(is_active=True)}
    weekly_max = defaultdict(int)
    for (tid, d), cap in state.capacities.items():
        if d in state.week:
            weekly_max[tid] += cap

    subjects = []
    for req in (
        SubjectRequirement.objects.filter(class_group=class_group)
        .select_related("subject", "teacher")
        .order_by("subject__name")
    ):
        subject = req.subject
        assigned = state.class_subject_count(class_group.pk, subject.pk, exclude)
        met = assigned >= req.periods_per_week
        candidates = sorted(
            (t for tid, t in teachers.items() if state.is_qualified(tid, subject.pk)),
            key=lambda t: (t.pk != req.teacher_id, t.name),
        )
        options = []
        for teacher in candidates:
            violations = [
                violation
                for violation in v.validate_assignment(
                    class_group, day, period, subject, teacher, exclude_entry_id=exclude, state=state
                )
                if violation.code in v.TEACHER_AVAILABILITY_CODES
            ]
            day_count = state.teacher_day_count(teacher.pk, day, exclude)
            week_count = state.teacher_week_count(teacher.pk) - (
                1 if entry and entry.teacher_id == teacher.pk else 0
            )
            options.append(
                {
                    "id": teacher.pk,
                    "name": teacher.name,
                    "designated": teacher.pk == req.teacher_id,
                    "is_cover": teacher.is_cover_teacher,
                    "status": "blocked" if violations else "available",
                    "reasons": [x.as_dict() for x in violations],
                    "day_load": {"assigned": day_count, "max": state.capacity(teacher.pk, day) or 0},
                    "week_load": {"assigned": week_count, "max": weekly_max[teacher.pk]},
                }
            )
        subjects.append(
            {
                "id": subject.pk,
                "name": subject.name,
                "code": subject.code or "",
                "required": req.periods_per_week,
                "assigned": assigned,
                "remaining": max(req.periods_per_week - assigned, 0),
                "met": met,
                "disabled_reason": (
                    f"All {req.periods_per_week} {subject.name} lessons are already placed."
                    if met
                    else ""
                ),
                "designated_teacher_id": req.teacher_id,
                "designated_teacher": req.teacher.name if req.teacher else "",
                "teachers": options,
            }
        )

    return {
        "class": {"id": class_group.pk, "label": str(class_group)},
        "day": {"value": day, "label": schedule.label},
        "period": {"number": period, "roman": roman(period)},
        "entry": (
            {
                "id": entry.pk,
                "updated_at": entry.updated_at.isoformat(),
                "subject_id": entry.subject_id,
                "subject": entry.subject.name,
                "teacher_id": entry.teacher_id,
                "teacher": entry.teacher.name,
                "is_locked": entry.is_locked,
            }
            if entry
            else None
        ),
        "subjects": subjects,
    }


# --------------------------------------------------------------------------
# Teacher workloads
# --------------------------------------------------------------------------

FREE, NEAR_FULL, FULL, OVER, NO_CAPACITY = "free", "near", "full", "over", "none"
LOAD_STATE_LABELS = {
    FREE: "Free",
    NEAR_FULL: "Near full",
    FULL: "Full",
    OVER: "Over capacity",
    NO_CAPACITY: "No capacity",
}


def load_state(assigned, maximum):
    if maximum is None:
        return OVER if assigned else NO_CAPACITY
    if assigned > maximum:
        return OVER
    if assigned == maximum:
        return FULL
    if maximum - assigned == 1 or assigned >= 0.75 * maximum:
        return NEAR_FULL
    return FREE


@dataclass
class LoadCell:
    day: int
    day_label: str
    assigned: int
    max: int | None

    @property
    def remaining(self):
        return max((self.max or 0) - self.assigned, 0)

    @property
    def state(self):
        return load_state(self.assigned, self.max)

    @property
    def state_label(self):
        return LOAD_STATE_LABELS[self.state]

    @property
    def label(self):
        return f"{self.assigned}/{self.max}" if self.max is not None else ("—" if not self.assigned else f"{self.assigned}/0")


@dataclass
class WorkloadRow:
    teacher: Teacher
    subjects: list
    cells: list

    @property
    def assigned(self):
        return sum(c.assigned for c in self.cells)

    @property
    def max(self):
        return sum(c.max or 0 for c in self.cells)

    @property
    def remaining(self):
        return max(self.max - self.assigned, 0)

    @property
    def percent(self):
        return round(100 * self.assigned / self.max) if self.max else (100 if self.assigned else 0)

    @property
    def state(self):
        if any(c.state == OVER for c in self.cells):
            return OVER
        return load_state(self.assigned, self.max if self.max else None)

    @property
    def state_label(self):
        return LOAD_STATE_LABELS[self.state]


WORKLOAD_SORTS = {
    "name": ("Name", lambda r: r.teacher.name.lower()),
    "load": ("Highest load", lambda r: (-r.percent, -r.assigned, r.teacher.name.lower())),
    "remaining": ("Most remaining", lambda r: (-r.remaining, r.teacher.name.lower())),
}


def teacher_workloads(*, subject_id=None, sort="name", teachers=None):
    """Teachers × operating days as assigned/max, across all classes."""
    week = get_week_schedule()
    if teachers is None:
        teachers = Teacher.objects.filter(is_active=True)
        if subject_id:
            teachers = teachers.filter(teacher_subjects__subject_id=subject_id)
    teachers = list(teachers.prefetch_related("subjects").distinct())
    ids = [t.pk for t in teachers]
    counts = defaultdict(int)
    for tid, day, n in (
        TimetableEntry.objects.filter(teacher_id__in=ids)
        .values_list("teacher_id", "day")
        .annotate(n=Count("id"))
    ):
        counts[(tid, day)] = n
    caps = {
        (t, d): m
        for t, d, m in TeacherDailyAvailability.objects.filter(teacher_id__in=ids).values_list(
            "teacher_id", "day", "max_periods"
        )
    }
    rows = [
        WorkloadRow(
            teacher,
            [s.name for s in teacher.subjects.all()],
            [LoadCell(s.day, s.short_label, counts[(teacher.pk, s.day)], caps.get((teacher.pk, s.day))) for s in week],
        )
        for teacher in teachers
    ]
    _, key = WORKLOAD_SORTS.get(sort, WORKLOAD_SORTS["name"])
    return week, sorted(rows, key=key)


# --------------------------------------------------------------------------
# Teacher timetable
# --------------------------------------------------------------------------


@dataclass
class TeacherRow:
    day: int
    label: str
    short_label: str
    cells: list
    load: LoadCell


def teacher_grid(teacher):
    week = get_week_schedule()
    width, recess_after, columns = grid_header(week)
    entries = defaultdict(dict)
    for entry in TimetableEntry.objects.with_related().filter(teacher=teacher):
        entries[entry.day][entry.period] = entry
    caps = dict(teacher.daily_availability.values_list("day", "max_periods"))
    rows = [
        TeacherRow(
            s.day,
            s.label,
            s.short_label,
            row_cells(s, width, recess_after, entries[s.day]),
            LoadCell(s.day, s.short_label, len(entries[s.day]), caps.get(s.day)),
        )
        for s in week
    ]
    filled = sum(len(by_period) for by_period in entries.values())
    return Grid(columns, rows, filled, sum(caps.get(s.day, 0) for s in week))


# --------------------------------------------------------------------------
# Teachers, subjects and requirements overviews
# --------------------------------------------------------------------------


def teacher_summaries():
    """Every teacher with subjects, weekly load and designated load."""
    _, rows = teacher_workloads(teachers=Teacher.objects.all())
    designated = defaultdict(int)
    for tid, periods in SubjectRequirement.objects.filter(teacher__isnull=False).values_list(
        "teacher_id", "periods_per_week"
    ):
        designated[tid] += periods
    for row in rows:
        row.designated = designated[row.teacher.pk]
    return rows


@dataclass
class SubjectSummary:
    subject: object
    teachers: list
    demand: int
    placed: int
    supply: int

    @property
    def shortfall(self):
        return max(self.demand - self.supply, 0)


def subject_summaries():
    from school.models import Subject

    week_days = {s.day for s in get_week_schedule()}
    weekly = defaultdict(int)
    for tid, day, cap in TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods"):
        if day in week_days:
            weekly[tid] += cap
    demand = defaultdict(int)
    for sid, periods in SubjectRequirement.objects.values_list("subject_id", "periods_per_week"):
        demand[sid] += periods
    placed = dict(
        TimetableEntry.objects.values_list("subject_id").annotate(n=Count("id")).values_list("subject_id", "n")
    )
    result = []
    for subject in Subject.objects.prefetch_related("teachers"):
        teachers = [t for t in subject.teachers.all() if t.is_active]
        result.append(
            SubjectSummary(
                subject,
                sorted(teachers, key=lambda t: t.name),
                demand[subject.pk],
                placed.get(subject.pk, 0),
                sum(weekly[t.pk] for t in teachers),
            )
        )
    return result


@dataclass
class RequirementRow:
    requirement: SubjectRequirement
    assigned: int
    teachers: list  # names of teachers actually teaching it

    @property
    def required(self):
        return self.requirement.periods_per_week

    @property
    def remaining(self):
        return max(self.required - self.assigned, 0)

    @property
    def is_over(self):
        return self.assigned > self.required

    @property
    def over_by(self):
        return max(self.assigned - self.required, 0)

    @property
    def is_met(self):
        return self.assigned >= self.required

    @property
    def designated(self):
        return self.requirement.teacher.name if self.requirement.teacher else ""

    @property
    def differs(self):
        return bool(self.designated) and any(name != self.designated for name in self.teachers)

    @property
    def percent(self):
        return min(round(100 * self.assigned / self.required), 100) if self.required else 0


def requirements_overview(*, grade_id=None, class_id=None):
    requirements = SubjectRequirement.objects.select_related(
        "class_group__grade", "class_group__section", "subject", "teacher"
    )
    if grade_id:
        requirements = requirements.filter(class_group__grade_id=grade_id)
    if class_id:
        requirements = requirements.filter(class_group_id=class_id)
    placed = defaultdict(list)
    for class_id_, subject_id, name in TimetableEntry.objects.values_list(
        "class_group_id", "subject_id", "teacher__name"
    ):
        placed[(class_id_, subject_id)].append(name)
    return [
        RequirementRow(r, len(placed[(r.class_group_id, r.subject_id)]), sorted(set(placed[(r.class_group_id, r.subject_id)])))
        for r in requirements
    ]


@dataclass
class ClassCompletion:
    class_group: object
    required: int
    assigned: int

    @property
    def percent(self):
        return min(round(100 * self.assigned / self.required), 100) if self.required else 0


def class_completion():
    from school.models import ClassGroup

    totals = defaultdict(lambda: [0, 0])
    for row in requirements_overview():
        totals[row.requirement.class_group_id][0] += row.required
        totals[row.requirement.class_group_id][1] += min(row.assigned, row.required)
    return [
        ClassCompletion(c, *totals[c.pk]) for c in ClassGroup.objects.with_structure()
    ]


# --------------------------------------------------------------------------
# Conflicts: lessons that break the rules after the configuration changed
# --------------------------------------------------------------------------

CONFLICT_KINDS = {
    v.TEACHER_INACTIVE: "Lessons taught by an inactive teacher",
    v.NOT_QUALIFIED: "Lessons taught by an unqualified teacher",
    v.SUBJECT_NOT_REQUIRED: "Lessons of a subject the class no longer takes",
    v.REQUIREMENT_MET: "More lessons than the requirement allows",
    v.CAPACITY_EXCEEDED: "Teachers over their daily capacity",
    v.NO_CAPACITY_CONFIGURED: "Lessons on a day the teacher is unavailable",
    v.INVALID_SLOT: "Lessons outside the school week",
    v.TEACHER_CLASH: "Teacher double-booked",
}


@dataclass
class SlotLink:
    class_id: int
    label: str
    day: int
    period: int


@dataclass
class Conflict:
    code: str
    message: str
    slots: list


@dataclass
class ConflictGroup:
    code: str
    title: str
    conflicts: list


def find_conflicts():
    """
    Re-check every live lesson against the current configuration with the
    same validators the builder uses. Per-lesson problems are listed per
    lesson; capacity and requirement overruns are grouped per teacher-day and
    per class-subject, since no single lesson is "the" wrong one.
    """
    from school.models import ClassGroup, Subject

    state = v.MemoryState.from_database()
    teachers = {t.pk: t for t in Teacher.objects.all()}
    classes = {c.pk: c for c in ClassGroup.objects.with_structure()}
    subjects = {s.pk: s for s in Subject.objects.all()}

    per_lesson = []
    grouped = defaultdict(list)  # (code, key) -> [lesson]
    for lesson in sorted(state.lessons.values(), key=lambda l: (l.class_label, l.day, l.period)):
        violations = v.validate_assignment(
            classes[lesson.class_id],
            lesson.day,
            lesson.period,
            subjects[lesson.subject_id],
            teachers[lesson.teacher_id],
            exclude_entry_id=lesson.key,
            state=state,
        )
        for violation in violations:
            if violation.code in (v.CAPACITY_EXCEEDED, v.NO_CAPACITY_CONFIGURED):
                grouped[(violation.code, (lesson.teacher_id, lesson.day))].append(lesson)
            elif violation.code == v.REQUIREMENT_MET:
                grouped[(violation.code, (lesson.class_id, lesson.subject_id))].append(lesson)
            else:
                per_lesson.append(
                    Conflict(
                        violation.code,
                        f"{lesson.class_label} {v.slot_text(lesson.day, lesson.period)}: {violation.message}",
                        [_link(lesson)],
                    )
                )

    conflicts = list(per_lesson)
    for (code, key), lessons in grouped.items():
        first = lessons[0]
        if code == v.REQUIREMENT_MET:
            required = state.requirement(*key)[0]
            message = (
                f"{first.class_label} has {len(lessons)} {first.subject_name} lessons but the "
                f"requirement is now {required}. Remove {len(lessons) - required}."
            )
        elif code == v.CAPACITY_EXCEEDED:
            cap = state.capacity(*key)
            message = (
                f"{first.teacher_name} has {len(lessons)} lessons on {day_label(first.day)} but may "
                f"now teach only {cap}. Move or remove {len(lessons) - cap}."
            )
        else:
            message = (
                f"{first.teacher_name} has {len(lessons)} lesson(s) on {day_label(first.day)} but "
                "no capacity is set for that day."
            )
        conflicts.append(Conflict(code, message, [_link(lesson) for lesson in lessons]))

    order = list(CONFLICT_KINDS)
    groups = defaultdict(list)
    for conflict in conflicts:
        groups[conflict.code].append(conflict)
    return [
        ConflictGroup(code, CONFLICT_KINDS.get(code, code), groups[code])
        for code in sorted(groups, key=lambda c: order.index(c) if c in order else len(order))
    ]


def conflict_count(groups=None):
    return sum(len(g.conflicts) for g in (find_conflicts() if groups is None else groups))


def _link(lesson):
    return SlotLink(
        lesson.class_id,
        f"{lesson.class_label} {day_label(lesson.day)[:3]} {roman(lesson.period)}",
        lesson.day,
        lesson.period,
    )

