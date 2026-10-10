"""
Pure scheduling rules. Nothing here writes to the database.

`validate_assignment()` answers "may this class have this subject with this
teacher in this slot?" and returns every blocking `Violation` (an empty list
means yes). `assignment_warnings()` returns the non-blocking ones.

The rules read the timetable through a *state* object:

- `DatabaseState` (the default) runs small queries against live rows. The
  service layer uses it inside its transaction, after locking teacher rows.
Teacher clashes are decided by clock time: a teacher may not have two lessons
whose times overlap, whatever their period numbers (see `school.day_schedule`).

- `MemoryState` holds a timetable in memory: either a snapshot of the live one
  (cheap bulk reads for the slot-options modal) or a hypothetical one (the
  generator re-validates a whole proposal with exactly the same rules).

Both expose the same small interface, so there is one implementation of each
rule. Lessons are identified by a key: the entry id for live rows, anything
hashable for hypothetical ones. `exclude_entry_id` removes one lesson from
consideration, which is how replacing a lesson avoids counting it twice.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from school.constants import Day
from school.day_schedule import Calendar, overlaps
from school.models import SubjectRequirement, TeacherDailyAvailability, TeacherSubject

from .models import Activity, TimetableEntry

# Blocking codes
INVALID_SLOT = "INVALID_SLOT"
SLOT_OCCUPIED = "SLOT_OCCUPIED"
TEACHER_INACTIVE = "TEACHER_INACTIVE"
NOT_QUALIFIED = "NOT_QUALIFIED"
SUBJECT_NOT_REQUIRED = "SUBJECT_NOT_REQUIRED"
REQUIREMENT_MET = "REQUIREMENT_MET"
TEACHER_CLASH = "TEACHER_CLASH"
NO_CAPACITY_CONFIGURED = "NO_CAPACITY_CONFIGURED"
CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
# Raised by the service layer, not by validate_assignment
STALE_SLOT = "STALE_SLOT"
SLOT_EMPTY = "SLOT_EMPTY"
# Warning only
DESIGNATED_TEACHER_MISMATCH = "DESIGNATED_TEACHER_MISMATCH"

# Violations that depend on the teacher rather than on the subject or the class.
TEACHER_AVAILABILITY_CODES = {TEACHER_CLASH, CAPACITY_EXCEEDED, NO_CAPACITY_CONFIGURED}


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    context: dict = field(default_factory=dict)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "context": self.context}


@dataclass(frozen=True)
class Lesson:
    """A lesson as the rules see it. `key` is the entry id for live rows."""

    key: object
    class_id: int
    day: int
    period: int
    subject_id: int
    teacher_id: int
    class_label: str = ""
    subject_name: str = ""
    teacher_name: str = ""
    is_extra: bool = False  # extra lessons never count towards a requirement


def slot_text(day, period):
    return f"{Day(day).label}, period {period}"


def timed_slot_text(state, class_id, day, period):
    """“Monday, period 3 (09:20–10:00)” — the times as that class's bells ring."""
    schedule = state.class_day_schedule(class_id, day)
    time = schedule.period_label(period) if schedule else ""
    return f"{slot_text(day, period)} ({time})" if time else slot_text(day, period)


def clash_violation(teacher, clash, state):
    where = timed_slot_text(state, clash.class_id, clash.day, clash.period)
    return Violation(
        TEACHER_CLASH,
        f"{teacher} is already teaching {clash.class_label} on {where}.",
        {"class_id": clash.class_id, "class": clash.class_label, "period": clash.period},
    )


def _first_overlap(calendar, lessons, class_id, day, period):
    """The first lesson (in another class) whose time overlaps the slot."""
    target = calendar.interval(class_id, day, period)
    if target is None:
        return None
    for lesson in sorted(lessons, key=lambda l: (l.period, l.class_label)):
        if lesson.class_id == class_id:
            continue
        other = calendar.interval(lesson.class_id, lesson.day, lesson.period)
        if other is not None and overlaps(target, other):
            return lesson
    return None


# --------------------------------------------------------------------------
# State implementations
# --------------------------------------------------------------------------


class DatabaseState:
    """Reads the live timetable with targeted queries."""

    def __init__(self, calendar=None):
        self._calendar = calendar

    @property
    def calendar(self):
        if self._calendar is None:
            self._calendar = Calendar.load()
        return self._calendar

    def class_day_schedule(self, class_id, day):
        return self.calendar.class_day(class_id, day)

    def _entries(self, exclude):
        qs = TimetableEntry.objects.all()
        return qs.exclude(pk=exclude) if exclude is not None else qs

    @staticmethod
    def _lesson(entry):
        return Lesson(
            key=entry.pk,
            class_id=entry.class_group_id,
            day=entry.day,
            period=entry.period,
            subject_id=entry.subject_id,
            teacher_id=entry.teacher_id,
            class_label=str(entry.class_group),
            subject_name=entry.subject.name,
            teacher_name=entry.teacher.name,
            is_extra=entry.is_extra,
        )

    def class_slot_lesson(self, class_id, day, period, exclude=None):
        entry = (
            self._entries(exclude)
            .with_related()
            .filter(class_group_id=class_id, day=day, period=period)
            .first()
        )
        return self._lesson(entry) if entry else None

    def class_activity(self, class_id, day, period):
        """The name of the activity holding this slot, or None."""
        return (
            Activity.objects.filter(class_group_id=class_id, day=day, period=period)
            .values_list("label", flat=True)
            .first()
        )

    def teacher_overlap(self, teacher_id, class_id, day, period, exclude=None):
        """The teacher's lesson in another class whose time overlaps this slot, if any."""
        entries = self._entries(exclude).with_related().filter(teacher_id=teacher_id, day=day)
        return _first_overlap(self.calendar, [self._lesson(e) for e in entries], class_id, day, period)

    def teacher_day_count(self, teacher_id, day, exclude=None):
        return self._entries(exclude).filter(teacher_id=teacher_id, day=day).count()

    def class_subject_count(self, class_id, subject_id, exclude=None):
        """Lessons counting towards the requirement (extra lessons do not)."""
        return (
            self._entries(exclude).filter(class_group_id=class_id, subject_id=subject_id, is_extra=False).count()
        )

    def capacity(self, teacher_id, day):
        record = TeacherDailyAvailability.objects.filter(teacher_id=teacher_id, day=day).first()
        return record.max_periods if record else None

    def requirement(self, class_id, subject_id):
        """(periods_per_week, designated teacher id) or None."""
        return (
            SubjectRequirement.objects.filter(class_group_id=class_id, subject_id=subject_id)
            .values_list("periods_per_week", "teacher_id")
            .first()
        )

    def is_qualified(self, teacher_id, subject_id):
        return TeacherSubject.objects.filter(teacher_id=teacher_id, subject_id=subject_id).exists()


class MemoryState:
    """
    A timetable held in memory. Configuration (week, capacities, requirements,
    qualifications) is loaded once; lessons can be added and removed freely.
    """

    def __init__(self, lessons=(), *, calendar=None, capacities=None, requirements=None, qualified=None, activities=None):
        self.calendar = calendar if calendar is not None else Calendar.load()
        self.activities = activities if activities is not None else _load_activities()
        self.week = {s.day: s for s in self.calendar.school_week}  # the operating days
        self.capacities = capacities if capacities is not None else _load_capacities()
        self.requirements = requirements if requirements is not None else _load_requirements()
        self.qualified = qualified if qualified is not None else _load_qualifications()
        self.lessons = {}
        self._class_slot = {}
        self._teacher_day = defaultdict(set)
        self._class_subject = defaultdict(set)
        for lesson in lessons:
            self.add(lesson)

    @classmethod
    def from_database(cls):
        entries = TimetableEntry.objects.with_related()
        return cls(DatabaseState._lesson(e) for e in entries)

    def add(self, lesson):
        self.lessons[lesson.key] = lesson
        self._class_slot[(lesson.class_id, lesson.day, lesson.period)] = lesson
        self._teacher_day[(lesson.teacher_id, lesson.day)].add(lesson.key)
        if not lesson.is_extra:
            self._class_subject[(lesson.class_id, lesson.subject_id)].add(lesson.key)

    def remove(self, key):
        lesson = self.lessons.pop(key)
        self._class_slot.pop((lesson.class_id, lesson.day, lesson.period), None)
        self._teacher_day[(lesson.teacher_id, lesson.day)].discard(key)
        self._class_subject[(lesson.class_id, lesson.subject_id)].discard(key)

    def class_day_schedule(self, class_id, day):
        return self.calendar.class_day(class_id, day)

    @staticmethod
    def _unless(lesson, exclude):
        return None if lesson is None or lesson.key == exclude else lesson

    def class_slot_lesson(self, class_id, day, period, exclude=None):
        return self._unless(self._class_slot.get((class_id, day, period)), exclude)

    def class_activity(self, class_id, day, period):
        return self.activities.get((class_id, day, period))

    def teacher_overlap(self, teacher_id, class_id, day, period, exclude=None):
        keys = self._teacher_day.get((teacher_id, day), ())
        lessons = [self.lessons[k] for k in keys if k != exclude]
        return _first_overlap(self.calendar, lessons, class_id, day, period)

    def teacher_day_count(self, teacher_id, day, exclude=None):
        keys = self._teacher_day.get((teacher_id, day), ())
        return len(keys) - (exclude in keys)

    def teacher_week_count(self, teacher_id):
        return sum(len(self._teacher_day.get((teacher_id, d), ())) for d in self.week)

    def class_subject_count(self, class_id, subject_id, exclude=None):
        keys = self._class_subject.get((class_id, subject_id), ())
        return len(keys) - (exclude in keys)

    def capacity(self, teacher_id, day):
        return self.capacities.get((teacher_id, day))

    def requirement(self, class_id, subject_id):
        return self.requirements.get((class_id, subject_id))

    def is_qualified(self, teacher_id, subject_id):
        return (teacher_id, subject_id) in self.qualified


def _load_activities():
    """{(class, day, period): activity name}"""
    return {(c, d, p): label for c, d, p, label in Activity.objects.values_list("class_group_id", "day", "period", "label")}


def _load_capacities():
    return {
        (t, d): m
        for t, d, m in TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods")
    }


def _load_requirements():
    return {
        (c, s): (p, t)
        for c, s, p, t in SubjectRequirement.objects.values_list(
            "class_group_id", "subject_id", "periods_per_week", "teacher_id"
        )
    }


def _load_qualifications():
    return set(TeacherSubject.objects.values_list("teacher_id", "subject_id"))


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def validate_assignment(
    class_group, day, period, subject, teacher, *, exclude_entry_id=None, state=None, extra=False
):
    """
    Every blocking reason why `teacher` may not teach `subject` to
    `class_group` in (`day`, `period`). Empty list means the lesson is allowed.

    `extra=True` is a lesson chosen on purpose beyond the requirements (an
    extra period of a subject, or cover with any subject the teacher is
    qualified for): the requirement checks are skipped; every other rule holds.

    `teacher.is_active` is read from the object passed in, so callers that
    hold a row lock should pass the locked instance.
    """
    state = state or DatabaseState()
    exclude = exclude_entry_id
    violations = []

    schedule = state.class_day_schedule(class_group.pk, day) if isinstance(day, int) else None
    slot_ok = schedule is not None and schedule.is_valid_period(period)
    if schedule is None:
        label = Day(day).label if day in Day.values else str(day)
        violations.append(
            Violation(INVALID_SLOT, f"The school does not operate on {label}.", {"day": day})
        )
    elif not slot_ok:
        violations.append(
            Violation(
                INVALID_SLOT,
                f"{class_group} has teaching periods 1–{schedule.periods} only on {schedule.label}.",
                {"day": day, "period": period},
            )
        )

    if slot_ok:
        activity = state.class_activity(class_group.pk, day, period)
        if activity:
            violations.append(
                Violation(
                    SLOT_OCCUPIED,
                    f"{class_group} has {activity} on {slot_text(day, period)}.",
                    {"activity": activity},
                )
            )
        occupant = state.class_slot_lesson(class_group.pk, day, period, exclude)
        if occupant:
            violations.append(
                Violation(
                    SLOT_OCCUPIED,
                    f"{class_group} already has {occupant.subject_name} with "
                    f"{occupant.teacher_name} on {slot_text(day, period)}.",
                    {"entry_id": occupant.key},
                )
            )

    if not teacher.is_active:
        violations.append(Violation(TEACHER_INACTIVE, f"{teacher} is inactive."))

    if not state.is_qualified(teacher.pk, subject.pk):
        violations.append(
            Violation(NOT_QUALIFIED, f"{teacher} is not qualified to teach {subject}.")
        )

    # An extra lesson is outside the requirements by choice, so they are not checked.
    requirement = None if extra else state.requirement(class_group.pk, subject.pk)
    if not extra and requirement is None:
        violations.append(
            Violation(
                SUBJECT_NOT_REQUIRED, f"{subject} is not one of {class_group}'s subjects."
            )
        )
    elif not extra:
        required = requirement[0]
        placed = state.class_subject_count(class_group.pk, subject.pk, exclude)
        if placed >= required:
            violations.append(
                Violation(
                    REQUIREMENT_MET,
                    f"{class_group} already has all {required} {subject} lessons this week.",
                    {"required": required, "assigned": placed},
                )
            )

    if slot_ok:
        # Clock time, not period number: timing groups ring different bells.
        clash = state.teacher_overlap(teacher.pk, class_group.pk, day, period, exclude)
        if clash:
            violations.append(clash_violation(teacher, clash, state))

        capacity = state.capacity(teacher.pk, day)
        if capacity is None:
            violations.append(
                Violation(
                    NO_CAPACITY_CONFIGURED,
                    f"{teacher} has no capacity set for {schedule.label}, so cannot teach that day.",
                )
            )
        else:
            assigned = state.teacher_day_count(teacher.pk, day, exclude)
            if assigned + 1 > capacity:
                violations.append(
                    Violation(
                        CAPACITY_EXCEEDED,
                        f"{teacher} already has {assigned} of a maximum {capacity} lessons "
                        f"on {schedule.label}.",
                        {"assigned": assigned, "max": capacity},
                    )
                )

    return violations


def assignment_warnings(class_group, subject, teacher, *, state=None):
    """Non-blocking concerns about an otherwise valid assignment."""
    state = state or DatabaseState()
    requirement = state.requirement(class_group.pk, subject.pk)
    if requirement and requirement[1] and requirement[1] != teacher.pk:
        from school.models import Teacher

        designated = Teacher.objects.filter(pk=requirement[1]).first()
        return [
            Violation(
                DESIGNATED_TEACHER_MISMATCH,
                f"{designated} is the designated {subject} teacher for {class_group}.",
                {"designated_teacher_id": requirement[1]},
            )
        ]
    return []
