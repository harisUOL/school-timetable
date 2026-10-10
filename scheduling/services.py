"""
The ONLY code that writes `TimetableEntry` rows.

The manual builder, management commands and the generator all call these
functions. Each one:

1. runs inside `transaction.atomic()`;
2. locks the affected teacher rows with `select_for_update()` in ascending
   id order before counting capacity (no deadlocks between two writers);
3. validates with `scheduling.validators` (a replacement excludes the entry
   being replaced, so its period is never counted twice);
4. turns an `IntegrityError` from the class-slot constraint into the same
   friendly message the validator gives (teacher clashes depend on clock
   times, so they are checked under the teacher row lock, not by a constraint);
5. writes a `TimetableAuditLog` row.

Optimistic concurrency: callers that act on an existing lesson pass
`expected={"id": ..., "updated_at": ...}` exactly as the client saw it. If the
slot changed since then, `SchedulingError` with `STALE_SLOT` is raised.

Extensibility: arguments are keyword-only and results are objects, so a
future `Lesson` owning several classes/teachers can be introduced behind
these functions without changing their callers.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction

from school.day_schedule import Calendar
from school.models import ClassGroup, Teacher

from . import validators as v
from .models import Activity, TimetableAuditLog, TimetableEntry


class SchedulingError(Exception):
    def __init__(self, violations):
        self.violations = list(violations)
        super().__init__("; ".join(violation.message for violation in self.violations))


@dataclass
class ServiceResult:
    entry: TimetableEntry | None
    action: str
    warnings: list = field(default_factory=list)


def snapshot(entry):
    if entry is None:
        return None
    return {
        "id": entry.pk,
        "class_group_id": entry.class_group_id,
        "class": str(entry.class_group),
        "day": entry.day,
        "period": entry.period,
        "subject_id": entry.subject_id,
        "subject": entry.subject.name,
        "teacher_id": entry.teacher_id,
        "teacher": entry.teacher.name,
        "is_locked": entry.is_locked,
    }


def entry_version(entry):
    """What a client must send back as `expected` to act on this entry."""
    return {"id": entry.pk, "updated_at": entry.updated_at.isoformat()}


def _stale(class_group, day, period):
    return SchedulingError(
        [
            v.Violation(
                v.STALE_SLOT,
                f"{class_group} on {v.slot_text(day, period)} was changed by someone else. "
                "Reload the timetable and try again.",
            )
        ]
    )


def _check_expected(existing, expected, class_group, day, period):
    if expected is None:
        return
    if (
        existing is None
        or str(existing.pk) != str(expected.get("id"))
        or existing.updated_at.isoformat() != expected.get("updated_at")
    ):
        raise _stale(class_group, day, period)


def _lock_teachers(*teacher_ids):
    ids = sorted({tid for tid in teacher_ids if tid is not None})
    return {t.pk: t for t in Teacher.objects.select_for_update().filter(pk__in=ids).order_by("pk")}


def _slot_entry(class_group, day, period):
    return (
        TimetableEntry.objects.select_for_update()
        .with_related()
        .filter(class_group=class_group, day=day, period=period)
        .first()
    )


def _audit(user, action, class_group, *, day=None, period=None, before=None, after=None):
    TimetableAuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        class_group=class_group,
        class_label=str(class_group),
        day=day,
        period=period,
        before=before,
        after=after,
    )


def _clash_from_integrity_error(class_group, day, period, teacher, exclude):
    """Explain a unique-constraint failure the way the validator would."""
    state = v.DatabaseState()
    clash = state.teacher_overlap(teacher.pk, class_group.pk, day, period, exclude)
    if clash:
        return v.clash_violation(teacher, clash, state)
    occupant = state.class_slot_lesson(class_group.pk, day, period, exclude)
    if occupant:
        return v.Violation(
            v.SLOT_OCCUPIED,
            f"{class_group} already has {occupant.subject_name} with {occupant.teacher_name} "
            f"on {v.slot_text(day, period)}.",
        )
    return v.Violation(
        v.TEACHER_CLASH, f"{teacher} cannot be booked on {v.slot_text(day, period)}."
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


@transaction.atomic
def assign_lesson(*, class_group, day, period, subject, teacher, user, expected=None):
    """
    Put `subject` with `teacher` into the class's slot.

    With `expected=None` the slot must be empty. To replace the lesson in an
    occupied slot, pass the `expected` version of that lesson.
    """
    existing = _slot_entry(class_group, day, period)
    _check_expected(existing, expected, class_group, day, period)
    replacing = existing if expected is not None else None

    locked = _lock_teachers(teacher.pk, replacing.teacher_id if replacing else None)
    teacher = locked.get(teacher.pk, teacher)
    exclude = replacing.pk if replacing else None

    violations = v.validate_assignment(
        class_group, day, period, subject, teacher, exclude_entry_id=exclude
    )
    if violations:
        raise SchedulingError(violations)

    before = snapshot(replacing)
    try:
        with transaction.atomic():
            if replacing:
                entry = replacing
                entry.subject = subject
                entry.teacher = teacher
                entry.updated_by = user if getattr(user, "is_authenticated", False) else None
                entry.save()
            else:
                actor = user if getattr(user, "is_authenticated", False) else None
                entry = TimetableEntry.objects.create(
                    class_group=class_group,
                    day=day,
                    period=period,
                    subject=subject,
                    teacher=teacher,
                    created_by=actor,
                    updated_by=actor,
                )
    except IntegrityError:
        raise SchedulingError(
            [_clash_from_integrity_error(class_group, day, period, teacher, exclude)]
        ) from None

    action = TimetableAuditLog.Action.REPLACE if replacing else TimetableAuditLog.Action.ASSIGN
    _audit(user, action, class_group, day=day, period=period, before=before, after=snapshot(entry))
    return ServiceResult(entry, action, v.assignment_warnings(class_group, subject, teacher))


@transaction.atomic
def clear_lesson(*, class_group, day, period, user, expected=None):
    existing = _slot_entry(class_group, day, period)
    _check_expected(existing, expected, class_group, day, period)
    if existing is None:
        raise SchedulingError(
            [v.Violation(v.SLOT_EMPTY, f"{class_group} has no lesson on {v.slot_text(day, period)}.")]
        )
    _lock_teachers(existing.teacher_id)
    before = snapshot(existing)
    existing.delete()
    _audit(user, TimetableAuditLog.Action.CLEAR, class_group, day=day, period=period, before=before)
    return ServiceResult(None, TimetableAuditLog.Action.CLEAR)


@transaction.atomic
def reset_class(*, class_group, user, keep_locked=False):
    """
    Remove this class's lessons (optionally keeping locked ones).

    Touches nothing else: other classes, capacities and requirements are left
    exactly as they are. The removed lessons are snapshotted into the audit log.
    Returns the number of lessons removed.
    """
    entries = TimetableEntry.objects.select_for_update().with_related().filter(class_group=class_group)
    if keep_locked:
        entries = entries.filter(is_locked=False)
    entries = list(entries.order_by("day", "period"))
    if not entries:
        return 0
    _lock_teachers(*(e.teacher_id for e in entries))
    before = [snapshot(e) for e in entries]
    TimetableEntry.objects.filter(pk__in=[e.pk for e in entries]).delete()
    _audit(user, TimetableAuditLog.Action.RESET, class_group, before=before, after={"keep_locked": keep_locked})
    return len(entries)


@transaction.atomic
def set_locked(*, entry, locked, user, expected=None):
    current = (
        TimetableEntry.objects.select_for_update().with_related().filter(pk=entry.pk).first()
    )
    if current is None:
        raise _stale(entry.class_group, entry.day, entry.period)
    _check_expected(current, expected, current.class_group, current.day, current.period)
    before = snapshot(current)
    current.is_locked = locked
    current.updated_by = user if getattr(user, "is_authenticated", False) else None
    current.save(update_fields=["is_locked", "updated_by", "updated_at"])
    action = TimetableAuditLog.Action.LOCK if locked else TimetableAuditLog.Action.UNLOCK
    _audit(
        user,
        action,
        current.class_group,
        day=current.day,
        period=current.period,
        before=before,
        after=snapshot(current),
    )
    return ServiceResult(current, action)


def record_bulk_change(*, action, user, summary):
    """Audit row for a change made of many service calls (e.g. applying a generated timetable)."""
    TimetableAuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        class_label="Whole school",
        after=summary,
    )


# --------------------------------------------------------------------------
# Activities: named blocks that are not lessons (assembly, activity session)
# --------------------------------------------------------------------------

ACTIVITY_SCOPES = ("class", "grade", "group", "school")
MAX_ACTIVITY_LABEL = 80


@dataclass
class ActivityResult:
    action: str
    label: str
    periods: list
    classes: list = field(default_factory=list)  # classes changed
    skipped: list = field(default_factory=list)  # classes whose day has no such periods
    cleared: int = 0  # lessons removed to make room


def activity_targets(class_group, scope, calendar=None):
    """The classes an activity added from `class_group` applies to, for each scope."""
    calendar = calendar or Calendar.load()
    classes = ClassGroup.objects.with_structure()
    if scope == "class":
        return [class_group]
    if scope == "grade":
        return list(classes.filter(grade_id=class_group.grade_id))
    if scope == "group":
        group = calendar.group_of_class(class_group.pk)
        return [c for c in classes if calendar.group_of_class(c.pk) == group]
    if scope == "school":
        return list(classes)
    raise SchedulingError([v.Violation("BAD_REQUEST", "Choose which classes the activity is for.")])


def _slot_list(day, periods):
    if len(periods) == 1:
        return v.slot_text(day, periods[0])
    return f"{v.slot_text(day, periods[0])}–{periods[-1]}"


@transaction.atomic
def add_activity(*, class_group, day, start, length, label, user, scope="class", clear_lessons=False):
    """
    Put an activity in `length` consecutive periods from `start`, for this
    class or every class in its grade, timing group or the whole school.
    Classes whose day has no such periods are skipped (never this class).

    The slots must be free. Lessons already there block the change unless
    `clear_lessons` is set: then they are removed (and audited) so the
    generator can place them elsewhere; locked lessons always block it.
    All or nothing.
    """
    label = " ".join(str(label or "").split())
    if not label:
        raise SchedulingError([v.Violation("ACTIVITY_NAME", "Give the activity a name.")])
    if len(label) > MAX_ACTIVITY_LABEL:
        raise SchedulingError(
            [v.Violation("ACTIVITY_NAME", f"Keep the name to {MAX_ACTIVITY_LABEL} characters or fewer.")]
        )
    if length < 1:
        raise SchedulingError([v.Violation("BAD_REQUEST", "An activity lasts at least one period.")])
    calendar = Calendar.load()
    periods = list(range(start, start + length))
    own = calendar.class_day(class_group.pk, day)
    if own is None or not all(own.is_valid_period(p) for p in periods):
        last = own.periods if own else 0
        raise SchedulingError(
            [v.Violation(v.INVALID_SLOT, f"{class_group} has teaching periods 1–{last} only on that day.")]
        )

    targets, skipped = [], []
    for c in activity_targets(class_group, scope, calendar):
        schedule = calendar.class_day(c.pk, day)
        (targets if schedule and periods[-1] <= schedule.periods else skipped).append(c)

    violations = []
    for a in Activity.objects.select_for_update().with_class().filter(class_group__in=targets, day=day, period__in=periods):
        violations.append(
            v.Violation(v.SLOT_OCCUPIED, f"{a.class_group} already has {a.label} on {v.slot_text(day, a.period)}.")
        )
    entries = list(
        TimetableEntry.objects.select_for_update()
        .with_related()
        .filter(class_group__in=targets, day=day, period__in=periods)
        .order_by("class_group__grade__order", "class_group__section__name", "period")
    )
    for e in entries:
        if e.is_locked:
            violations.append(
                v.Violation(
                    v.SLOT_OCCUPIED,
                    f"{e.class_group} has a locked lesson ({e.subject} with {e.teacher}) on "
                    f"{v.slot_text(day, e.period)}. Unlock it first.",
                )
            )
    if entries and not clear_lessons:
        examples = ", ".join(f"{e.class_group} {e.subject.code or e.subject}" for e in entries[:4])
        more = f" and {len(entries) - 4} more" if len(entries) > 4 else ""
        violations.append(
            v.Violation(
                v.SLOT_OCCUPIED,
                f"{len(entries)} lesson(s) are already in those periods ({examples}{more}). "
                "Tick “Remove the lessons in those periods” to make room; the generator can place them again.",
                {"lessons": len(entries)},
            )
        )
    if violations:
        if len(violations) > 8:
            violations = violations[:8] + [v.Violation(v.SLOT_OCCUPIED, f"…and {len(violations) - 8} more.")]
        raise SchedulingError(violations)

    if entries:
        _lock_teachers(*(e.teacher_id for e in entries))
        for e in entries:
            before = snapshot(e)
            e.delete()
            _audit(user, TimetableAuditLog.Action.CLEAR, e.class_group, day=day, period=e.period, before=before)

    batch = uuid.uuid4()
    actor = user if getattr(user, "is_authenticated", False) else None
    Activity.objects.bulk_create(
        Activity(class_group=c, day=day, period=p, label=label, batch=batch, created_by=actor)
        for c in targets
        for p in periods
    )
    for c in targets:
        _audit(
            user,
            TimetableAuditLog.Action.ACTIVITY_ADD,
            c,
            day=day,
            period=start,
            after={"activity": label, "periods": periods, "batch": str(batch)},
        )
    return ActivityResult(TimetableAuditLog.Action.ACTIVITY_ADD, label, periods, targets, skipped, len(entries))


@transaction.atomic
def remove_activity(*, class_group, day, period, user, everywhere=False):
    """
    Remove the activity in this slot: all of its periods in this class, or
    (`everywhere`) every class and period it was added to together.
    """
    activity = (
        Activity.objects.select_for_update().filter(class_group=class_group, day=day, period=period).first()
    )
    if activity is None:
        raise SchedulingError(
            [v.Violation(v.SLOT_EMPTY, f"{class_group} has no activity on {v.slot_text(day, period)}.")]
        )
    rows = Activity.objects.select_for_update().with_class().filter(batch=activity.batch)
    if not everywhere:
        rows = rows.filter(class_group=class_group)
    by_class = defaultdict(list)
    for row in rows:
        by_class[row.class_group].append(row.period)
    for c, periods in by_class.items():
        _audit(
            user,
            TimetableAuditLog.Action.ACTIVITY_REMOVE,
            c,
            day=day,
            period=min(periods),
            before={"activity": activity.label, "periods": sorted(periods), "batch": str(activity.batch)},
        )
    Activity.objects.filter(pk__in=[r.pk for r in rows]).delete()
    periods = sorted({p for ps in by_class.values() for p in ps})
    return ActivityResult(TimetableAuditLog.Action.ACTIVITY_REMOVE, activity.label, periods, list(by_class))
