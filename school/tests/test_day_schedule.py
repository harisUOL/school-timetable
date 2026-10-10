from django.core.exceptions import ValidationError
from django.test import TestCase

from scheduling.models import TimetableEntry
from school.constants import Day
from school.day_schedule import (
    get_day_schedule,
    get_week_schedule,
    is_valid_slot,
    periods_for_day,
    weekly_teaching_slots,
)
from school.models import SchoolDayConfiguration, SchoolSettings

from .factories import make_class, make_requirement, make_subject, make_teacher


class SeededCalendarTests(TestCase):
    """The data migration seeds Monday–Saturday, 9 periods, recess after 4."""

    def test_default_week(self):
        week = get_week_schedule()
        self.assertEqual([d.day for d in week], list(range(Day.MONDAY, Day.SUNDAY)))
        self.assertTrue(all(d.periods == 9 and d.recess_after == 4 for d in week))
        self.assertEqual(weekly_teaching_slots(), 54)

    def test_recess_is_not_a_period(self):
        monday = get_day_schedule(Day.MONDAY)
        self.assertTrue(monday.has_recess)
        self.assertEqual(list(monday.period_numbers), list(range(1, 10)))

    def test_sunday_is_closed(self):
        self.assertIsNone(get_day_schedule(Day.SUNDAY))
        self.assertEqual(periods_for_day(Day.SUNDAY), 0)
        self.assertFalse(is_valid_slot(Day.SUNDAY, 1))

    def test_slot_validity(self):
        self.assertTrue(is_valid_slot(Day.MONDAY, 1))
        self.assertTrue(is_valid_slot(Day.MONDAY, 9))
        self.assertFalse(is_valid_slot(Day.MONDAY, 0))
        self.assertFalse(is_valid_slot(Day.MONDAY, 10))


class DayOverrideTests(TestCase):
    def setUp(self):
        self.saturday = SchoolDayConfiguration.objects.get(day=Day.SATURDAY)

    def test_saturday_with_six_periods(self):
        self.saturday.periods = 6
        self.saturday.full_clean()
        self.saturday.save()

        self.assertEqual(periods_for_day(Day.SATURDAY), 6)
        self.assertEqual(periods_for_day(Day.FRIDAY), 9)
        self.assertEqual(weekly_teaching_slots(), 5 * 9 + 6)
        self.assertFalse(is_valid_slot(Day.SATURDAY, 7))

    def test_closing_a_day_removes_it(self):
        self.saturday.is_operating = False
        self.saturday.full_clean()
        self.saturday.save()
        self.assertEqual(len(get_week_schedule()), 5)
        self.assertEqual(weekly_teaching_slots(), 45)

    def test_recess_must_fall_inside_the_day(self):
        self.saturday.periods = 4  # inherited recess-after-4 is now impossible
        with self.assertRaises(ValidationError):
            self.saturday.full_clean()

        self.saturday.recess_after_period = 0  # no recess on a 4-period day
        self.saturday.full_clean()

    def test_cannot_shorten_day_below_scheduled_lessons(self):
        maths = make_subject("Mathematics")
        teacher = make_teacher(subjects=[maths], capacity=9)
        class_group = make_class()
        make_requirement(class_group, maths, periods=6)
        TimetableEntry.objects.create(
            class_group=class_group, day=Day.SATURDAY, period=8, teacher=teacher, subject=maths
        )

        self.saturday.periods = 6
        with self.assertRaisesMessage(ValidationError, "beyond period 6"):
            self.saturday.full_clean()

    def test_cannot_shorten_day_below_teacher_capacity(self):
        make_teacher(capacity={Day.SATURDAY: 8})
        self.saturday.periods = 6
        with self.assertRaisesMessage(ValidationError, "allow more than 6"):
            self.saturday.full_clean()


class SchoolSettingsTests(TestCase):
    def test_singleton(self):
        settings = SchoolSettings.load()
        self.assertEqual(settings.pk, 1)

        other = SchoolSettings(school_name="Another")
        other.save()
        self.assertEqual(SchoolSettings.objects.count(), 1)
        self.assertEqual(SchoolSettings.load().school_name, "Another")

    def test_cannot_delete(self):
        with self.assertRaises(ValidationError):
            SchoolSettings.load().delete()

    def test_changing_defaults_revalidates_inheriting_days(self):
        settings = SchoolSettings.load()
        settings.default_periods_per_day = 3  # recess after 4 no longer fits
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_default_change_flows_to_inheriting_days(self):
        settings = SchoolSettings.load()
        settings.default_periods_per_day = 8
        settings.full_clean()
        settings.save()
        self.assertEqual(weekly_teaching_slots(), 48)
