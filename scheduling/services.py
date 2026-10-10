"""
The ONLY code that writes `TimetableEntry` rows.

The manual builder, management commands and the generator all call these
functions. Each one:

1. runs inside `transaction.atomic()`;
2. locks the affected teacher rows with `select_for_update()` in ascending
   id order before counting capacity (no deadlocks between two writers);
3. validates with `scheduling.validators` (a replacement excludes the entry
   being replaced, so its period is never counted twice);
4. turns an `IntegrityError` from the uniqueness constraints into the same
   friendly clash message the validator gives;
5. writes a `TimetableAuditLog` row.

Optimistic concurrency: callers that act on an existing lesson pass
`expected={"id": ..., "updated_at": ...}` exactly as the client saw it. If the
slot changed since then, `SchedulingError` with `STALE_SLOT` is raised.

Extensibility: arguments are keyword-only and results are objects, so a
future `Lesson` owning several classes/teachers can be introduced behind
these functions without changing their callers.
"""

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction

from school.models import Teacher

from . import validators as v
from .models import TimetableAuditLog, TimetableEntry


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
    clash = state.teacher_slot_lesson(teacher.pk, day, period, exclude)
    if clash:
        message = f"{teacher} is already teaching {clash.class_label} on {v.slot_text(day, period)}."
        return v.Violation(v.TEACHER_CLASH, message, {"class_id": clash.class_id, "class": clash.class_label})
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
