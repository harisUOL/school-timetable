"""
Database-level guarantees for TimetableEntry.

These are the last line of defence. The friendly, cross-table validation
(capacity, qualification, requirements) arrives with the service layer in
Phase 3 and is tested there.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from scheduling.models import TimetableEntry
from school.constants import Day
from school.models import SchoolDayConfiguration
from school.tests.factories import make_class, make_requirement, make_subject, make_teacher


class TimetableEntryConstraintTests(TestCase):
    def setUp(self):
        self.maths = make_subject("Mathematics")
        self.physics = make_subject("Physics")
        self.teacher_a = make_teacher("Teacher A", subjects=[self.maths, self.physics], capacity=6)
        self.teacher_b = make_teacher("Teacher B", subjects=[self.maths], capacity=6)
        self.class_10a = make_class("10", "A")
        self.class_10b = make_class("10", "B")
        for class_group in (self.class_10a, self.class_10b):
            make_requirement(class_group, self.maths, periods=6)

    def entry(self, **overrides):
        values = {
            "class_group": self.class_10a,
            "day": Day.MONDAY,
            "period": 3,
            "teacher": self.teacher_a,
            "subject": self.maths,
        }
        values.update(overrides)
        return TimetableEntry(**values)

    def test_valid_entry(self):
        entry = self.entry()
        entry.full_clean()
        entry.save()
        self.assertEqual(str(entry), "10A · Monday P3 · Mathematics (Teacher A)")

    def test_class_cannot_have_two_lessons_in_one_slot(self):
        self.entry().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry(teacher=self.teacher_b).save()

    def test_teacher_cannot_be_in_two_classes_at_once(self):
        self.entry().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry(class_group=self.class_10b).save()

    def test_same_teacher_different_period_is_fine(self):
        self.entry().save()
        self.entry(class_group=self.class_10b, period=4).save()
        self.assertEqual(TimetableEntry.objects.filter(teacher=self.teacher_a).count(), 2)

    def test_period_zero_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry(period=0).save()

    def test_invalid_day_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry(day=7).save()

    def test_period_beyond_day_length_rejected(self):
        with self.assertRaisesMessage(ValidationError, "periods 1–9"):
            self.entry(period=10).full_clean()

    def test_period_beyond_short_day_rejected(self):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)
        self.entry(day=Day.SATURDAY, period=6).full_clean()
        with self.assertRaises(ValidationError):
            self.entry(day=Day.SATURDAY, period=7).full_clean()

    def test_non_operating_day_rejected(self):
        with self.assertRaisesMessage(ValidationError, "does not operate"):
            self.entry(day=Day.SUNDAY, period=1).full_clean()

    def test_teacher_with_lessons_cannot_be_deleted(self):
        self.entry().save()
        with self.assertRaises(ProtectedError):
            self.teacher_a.delete()

    def test_deleting_class_removes_only_its_lessons(self):
        self.entry().save()
        self.entry(class_group=self.class_10b, teacher=self.teacher_b).save()
        self.class_10a.delete()
        self.assertEqual(TimetableEntry.objects.count(), 1)
        self.assertEqual(TimetableEntry.objects.get().class_group, self.class_10b)
