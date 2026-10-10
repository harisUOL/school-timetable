from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from school import demo_data
from school.constants import Day
from school.day_schedule import get_day_schedule, weekly_teaching_slots
from school.health import run_health_checks
from school.models import (
    ClassGroup,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
    TeacherSubject,
)

from .factories import make_class, make_requirement, make_subject, make_teacher


def seed(*args):
    call_command("seed_demo", *args, stdout=StringIO(), stderr=StringIO())


def snapshot():
    return {
        "classes": ClassGroup.objects.count(),
        "subjects": Subject.objects.count(),
        "teachers": Teacher.objects.count(),
        "qualifications": TeacherSubject.objects.count(),
        "capacities": sorted(
            TeacherDailyAvailability.objects.values_list("teacher__name", "day", "max_periods")
        ),
        "requirements": sorted(
            SubjectRequirement.objects.values_list(
                "class_group__grade__name",
                "class_group__section__name",
                "subject__code",
                "periods_per_week",
                "teacher__name",
            ),
            key=str,
        ),
    }


class SeedDemoTests(TestCase):
    def test_creates_the_demo_school(self):
        seed()
        self.assertEqual(ClassGroup.objects.count(), 10)
        self.assertEqual(Subject.objects.count(), 12)
        self.assertGreaterEqual(Teacher.objects.count(), 25)
        self.assertTrue(Teacher.objects.filter(is_cover_teacher=True).exists())
        multi = [t for t in Teacher.objects.all() if t.subjects.count() >= 2]
        self.assertGreater(len(multi), 10)
        self.assertEqual(get_day_schedule(Day.SATURDAY).periods, 6)
        self.assertTrue(Subject.objects.exclude(code=None).count() == 12)

    def test_is_idempotent(self):
        seed()
        first = snapshot()
        seed()
        self.assertEqual(snapshot(), first)

    def test_data_is_feasible(self):
        seed()
        self.assertEqual([i.message for i in run_health_checks() if i.is_error], [])
        slots = weekly_teaching_slots()
        for class_group in ClassGroup.objects.all():
            self.assertLessEqual(class_group.total_required_periods(), slots)
        designated = SubjectRequirement.objects.filter(teacher__isnull=False).count()
        self.assertGreater(designated, SubjectRequirement.objects.count() * 0.8)

    def test_capacities_vary(self):
        seed()
        weekly = {
            sum(t.daily_availability.values_list("max_periods", flat=True))
            for t in Teacher.objects.all()
        }
        self.assertGreater(len(weekly), 3)

    def test_reset_removes_only_demo_data(self):
        own_class = make_class("7", "Z")
        own_subject = make_subject("Art", "ART")
        own_teacher = make_teacher("Own Teacher", subjects=[own_subject], capacity=3)
        make_requirement(own_class, own_subject, periods=2, teacher=own_teacher)
        seed()
        seed("--remove")
        self.assertEqual(list(ClassGroup.objects.all()), [own_class])
        self.assertEqual(list(Teacher.objects.all()), [own_teacher])
        self.assertEqual(list(Subject.objects.all()), [own_subject])
        self.assertEqual(SubjectRequirement.objects.count(), 1)

        seed("--reset")
        self.assertEqual(ClassGroup.objects.count(), 11)
        self.assertEqual(Teacher.objects.count(), len(demo_data.TEACHERS) + 1)

    def test_reset_restores_edited_demo_records(self):
        seed()
        before = snapshot()
        SubjectRequirement.objects.filter(subject__code="MATH").update(periods_per_week=1)
        seed("--reset")
        self.assertEqual(snapshot(), before)

    def test_with_users_creates_role_accounts(self):
        seed("--with-users")
        from django.contrib.auth import get_user_model

        editor = get_user_model().objects.get(username="demo_editor")
        viewer = get_user_model().objects.get(username="demo_viewer")
        self.assertTrue(editor.has_perm("scheduling.change_timetableentry"))
        self.assertFalse(viewer.has_perm("scheduling.change_timetableentry"))
