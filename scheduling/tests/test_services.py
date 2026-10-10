from unittest import mock

from django.db import transaction
from django.test import TestCase

from school.constants import Day
from school.models import SubjectRequirement, TeacherDailyAvailability
from scheduling import services
from scheduling import validators as v
from scheduling.models import TimetableAuditLog, TimetableEntry

from .fixtures import SchoolFixture


def codes(exc):
    return [violation.code for violation in exc.exception.violations]


class AssignLessonTests(SchoolFixture):
    def test_valid_assignment(self):
        result = self.assign()
        entry = result.entry
        self.assertEqual(
            (entry.class_group, entry.day, entry.period, entry.subject, entry.teacher),
            (self.c10a, Day.MONDAY, 1, self.maths, self.ms_maths),
        )
        self.assertEqual(result.warnings, [])
        log = TimetableAuditLog.objects.get()
        self.assertEqual(log.action, "assign")
        self.assertEqual(log.after["teacher"], "Ms Maths")
        self.assertEqual(log.user, self.editor)

    def test_double_booked_teacher_names_the_other_class(self):
        self.assign(self.c10b, teacher=self.mr_physics)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.c10a, teacher=self.mr_physics)
        self.assertEqual(codes(exc), [v.TEACHER_CLASH])
        self.assertEqual(
            exc.exception.violations[0].message,
            "Mr Physics is already teaching 10B on Monday, period 1.",
        )
        self.assertEqual(exc.exception.violations[0].context["class"], "10B")

    def test_duplicate_slot_without_replace_is_rejected(self):
        self.assign()
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(teacher=self.mr_physics)
        self.assertEqual(codes(exc), [v.SLOT_OCCUPIED])
        self.assertEqual(TimetableEntry.objects.count(), 1)

    def test_daily_limit_counts_lessons_across_all_classes(self):
        # Ms Monday may teach 2 lessons on Monday, in any classes.
        self.assign(self.c10a, period=1, teacher=self.ms_monday)
        self.assign(self.c10b, period=2, teacher=self.ms_monday)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.c10b, period=3, teacher=self.ms_monday)
        self.assertEqual(codes(exc), [v.CAPACITY_EXCEEDED])
        self.assertIn("2 of a maximum 2 lessons on Monday", str(exc.exception))

    def test_unqualified_teacher(self):
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(teacher=self.mr_bio)
        self.assertEqual(codes(exc), [v.NOT_QUALIFIED])
        self.assertEqual(str(exc.exception), "Mr Unqualified is not qualified to teach Mathematics.")

    def test_inactive_teacher(self):
        self.ms_maths.is_active = False
        self.ms_maths.save()
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign()
        self.assertEqual(codes(exc), [v.TEACHER_INACTIVE])

    def test_missing_capacity_record_means_unavailable(self):
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(day=Day.TUESDAY, teacher=self.ms_monday)
        self.assertEqual(codes(exc), [v.NO_CAPACITY_CONFIGURED])

    def test_zero_capacity_blocks(self):
        TeacherDailyAvailability.objects.filter(teacher=self.ms_maths, day=Day.MONDAY).update(max_periods=0)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign()
        self.assertEqual(codes(exc), [v.CAPACITY_EXCEEDED])

    def test_subject_not_required(self):
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.c10b, subject=self.physics, teacher=self.mr_physics)
        self.assertEqual(codes(exc), [v.SUBJECT_NOT_REQUIRED])

    def test_requirement_already_met(self):
        self.assign(subject=self.physics, teacher=self.mr_physics, period=1)
        self.assign(subject=self.physics, teacher=self.mr_physics, period=2)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(subject=self.physics, teacher=self.mr_physics, period=3)
        self.assertEqual(codes(exc), [v.REQUIREMENT_MET])
        self.assertIn("already has all 2 Physics lessons", str(exc.exception))

    def test_designated_teacher_mismatch_is_only_a_warning(self):
        result = self.assign(teacher=self.mr_physics)
        self.assertIsNotNone(result.entry.pk)
        self.assertEqual([w.code for w in result.warnings], [v.DESIGNATED_TEACHER_MISMATCH])
        self.assertIn("Ms Maths is the designated Mathematics teacher for 10A", result.warnings[0].message)

    def test_several_violations_are_reported_together(self):
        self.ms_monday.is_active = False
        self.ms_monday.save()
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.c10b, day=Day.TUESDAY, subject=self.physics, teacher=self.ms_monday)
        self.assertEqual(
            set(codes(exc)),
            {v.TEACHER_INACTIVE, v.NOT_QUALIFIED, v.SUBJECT_NOT_REQUIRED, v.NO_CAPACITY_CONFIGURED},
        )


class InvalidSlotTests(SchoolFixture):
    def assert_invalid(self, day, period, message):
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(day=day, period=period)
        self.assertEqual(codes(exc), [v.INVALID_SLOT])
        self.assertIn(message, str(exc.exception))

    def test_non_operating_day(self):
        self.assert_invalid(Day.SUNDAY, 1, "does not operate on Sunday")

    def test_unknown_day(self):
        self.assert_invalid(9, 1, "does not operate")

    def test_period_out_of_range(self):
        self.assert_invalid(Day.MONDAY, 0, "periods 1–9")
        self.assert_invalid(Day.MONDAY, 10, "periods 1–9")

    def test_period_beyond_short_saturday(self):
        self.assert_invalid(Day.SATURDAY, 7, "Saturday has teaching periods 1–6 only")
        self.assign(day=Day.SATURDAY, period=6)  # the last real period is fine


class ReplaceTests(SchoolFixture):
    def test_replace_keeping_same_teacher_costs_no_extra_capacity(self):
        # Ms Maths: 3 a day. Fill Monday completely, then replace one of them.
        first = self.assign(period=1).entry
        self.assign(period=2)
        self.assign(self.c10b, period=3)
        result = self.assign(period=1, expected=services.entry_version(first))
        self.assertEqual(result.action, "replace")
        self.assertEqual(result.entry.pk, first.pk)
        self.assertEqual(TimetableEntry.objects.filter(teacher=self.ms_maths, day=Day.MONDAY).count(), 3)

    def test_replace_with_new_teacher_frees_old_teachers_capacity(self):
        # Ms Monday is full (2/2). Moving one of her lessons to Mr Physics frees a lesson.
        first = self.assign(self.c10a, period=1, teacher=self.ms_monday).entry
        self.assign(self.c10b, period=2, teacher=self.ms_monday)
        self.assign(self.c10a, period=1, teacher=self.mr_physics, expected=services.entry_version(first))
        self.assertEqual(TimetableEntry.objects.filter(teacher=self.ms_monday, day=Day.MONDAY).count(), 1)
        self.assign(self.c10b, period=3, teacher=self.ms_monday)  # fits again

    def test_replace_into_a_full_teacher_is_rejected(self):
        self.assign(self.c10a, period=1, teacher=self.ms_monday)
        self.assign(self.c10b, period=2, teacher=self.ms_monday)
        other = self.assign(self.c10a, period=3, teacher=self.mr_physics).entry
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.c10a, period=3, teacher=self.ms_monday, expected=services.entry_version(other))
        self.assertEqual(codes(exc), [v.CAPACITY_EXCEEDED])

    def test_replace_when_requirement_is_met_by_the_replaced_lesson(self):
        lessons = [self.assign(subject=self.physics, teacher=self.mr_physics, period=p).entry for p in (1, 2)]
        # Swapping the teacher of an existing Physics lesson is fine: it does not add a lesson.
        self.assign(
            subject=self.physics, teacher=self.mr_physics, period=2, expected=services.entry_version(lessons[1])
        )

    def test_stale_replace_is_rejected(self):
        entry = self.assign().entry
        seen = services.entry_version(entry)
        self.assign(teacher=self.mr_physics, expected=seen)  # someone else changes it first
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(teacher=self.ms_maths, expected=seen)
        self.assertEqual(codes(exc), [v.STALE_SLOT])
        self.assertEqual(TimetableEntry.objects.get().teacher, self.mr_physics)

    def test_stale_when_slot_was_cleared(self):
        entry = self.assign().entry
        seen = services.entry_version(entry)
        services.clear_lesson(class_group=self.c10a, day=Day.MONDAY, period=1, user=self.editor)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(teacher=self.mr_physics, expected=seen)
        self.assertEqual(codes(exc), [v.STALE_SLOT])


class ClearLockResetTests(SchoolFixture):
    def test_clear_lesson(self):
        entry = self.assign().entry
        services.clear_lesson(
            class_group=self.c10a, day=Day.MONDAY, period=1, user=self.editor,
            expected=services.entry_version(entry),
        )
        self.assertFalse(TimetableEntry.objects.exists())
        log = TimetableAuditLog.objects.first()
        self.assertEqual((log.action, log.before["subject"]), ("clear", "Mathematics"))

    def test_clear_empty_slot(self):
        with self.assertRaises(services.SchedulingError) as exc:
            services.clear_lesson(class_group=self.c10a, day=Day.MONDAY, period=1, user=self.editor)
        self.assertEqual(codes(exc), [v.SLOT_EMPTY])

    def test_lock_and_unlock(self):
        entry = self.assign().entry
        services.set_locked(entry=entry, locked=True, user=self.editor)
        entry.refresh_from_db()
        self.assertTrue(entry.is_locked)
        services.set_locked(entry=entry, locked=False, user=self.editor, expected=services.entry_version(entry))
        entry.refresh_from_db()
        self.assertFalse(entry.is_locked)
        self.assertEqual(
            list(TimetableAuditLog.objects.values_list("action", flat=True)), ["unlock", "lock", "assign"]
        )

    def test_reset_class_touches_nothing_else(self):
        self.assign(self.c10a, period=1)
        self.assign(self.c10a, period=2, subject=self.physics, teacher=self.mr_physics)
        kept = self.assign(self.c10b, period=1, teacher=self.mr_physics).entry
        capacities = list(TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods"))
        requirements = list(SubjectRequirement.objects.values_list("pk", "periods_per_week", "teacher_id"))

        removed = services.reset_class(class_group=self.c10a, user=self.editor)

        self.assertEqual(removed, 2)
        self.assertEqual(list(TimetableEntry.objects.all()), [kept])
        self.assertEqual(
            list(TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods")), capacities
        )
        self.assertEqual(list(SubjectRequirement.objects.values_list("pk", "periods_per_week", "teacher_id")), requirements)
        log = TimetableAuditLog.objects.filter(action="reset").get()
        self.assertEqual(log.class_label, "10A")
        self.assertEqual([s["subject"] for s in log.before], ["Mathematics", "Physics"])

    def test_reset_can_keep_locked_lessons(self):
        locked = self.assign(period=1).entry
        services.set_locked(entry=locked, locked=True, user=self.editor)
        self.assign(period=2)
        self.assertEqual(services.reset_class(class_group=self.c10a, user=self.editor, keep_locked=True), 1)
        self.assertEqual(list(TimetableEntry.objects.all()), [locked])


class IntegrityErrorTests(SchoolFixture):
    def test_unique_constraint_failure_becomes_a_friendly_clash(self):
        """If two writers race past validation, the database constraint still gives a clear message."""
        self.assign(self.c10b, teacher=self.mr_physics)
        with mock.patch.object(v, "validate_assignment", return_value=[]):
            with self.assertRaises(services.SchedulingError) as exc:
                self.assign(self.c10a, teacher=self.mr_physics)
        self.assertEqual(codes(exc), [v.TEACHER_CLASH])
        self.assertIn("already teaching 10B on Monday, period 1", str(exc.exception))
        self.assertEqual(TimetableEntry.objects.count(), 1)


class TransactionTests(TestCase):
    def test_service_rolls_back_inside_outer_transaction(self):
        """A failed call leaves no partial writes, even inside a caller's transaction."""
        from school.tests.factories import make_class, make_requirement, make_subject, make_teacher

        cls = make_class()
        subject = make_subject()
        teacher = make_teacher(subjects=[subject], capacity=1)
        make_requirement(cls, subject, periods=5)
        with transaction.atomic():
            services.assign_lesson(class_group=cls, day=0, period=1, subject=subject, teacher=teacher, user=None)
            with self.assertRaises(services.SchedulingError):
                services.assign_lesson(class_group=cls, day=0, period=2, subject=subject, teacher=teacher, user=None)
        self.assertEqual(TimetableEntry.objects.count(), 1)
        self.assertEqual(TimetableAuditLog.objects.count(), 1)
