"""
Read-side queries for the timetable. Results are shaped for templates
(rows of cells, flat records), so templates never need dictionary lookups.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Count

from school.day_schedule import get_week_schedule
from school.models import SubjectRequirement, Teacher, TeacherDailyAvailability

from . import validators as v
from .models import TimetableEntry

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


def _row_cells(schedule, width, recess_after, entries_by_period):
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


def _header(week):
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
    width, recess_after, columns = _header(week)
    entries = defaultdict(dict)
    for entry in TimetableEntry.objects.with_related().filter(class_group=class_group):
        entries[entry.day][entry.period] = entry
    rows = [
        Row(s.day, s.label, s.short_label, _row_cells(s, width, recess_after, entries[s.day]))
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
