from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from school.constants import Day
from school.models import (
    SchoolDayConfiguration,
    SubjectRequirement,
    TeacherDailyAvailability,
    TeacherSubject,
)

from .factories import make_class, make_requirement, make_subject, make_teacher


class AdminTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(self.user)


class AdminPagesLoadTests(AdminTestCase):
    """Every admin page we rely on renders (guards against missing templates)."""

    def test_changelists_and_add_pages(self):
        make_requirement(make_class(), make_subject(), periods=5, teacher=None)
        make_teacher(subjects=[make_subject("Physics")], capacity=5)
        for model in [
            "schoolsettings",
            "schooldayconfiguration",
            "grade",
            "section",
            "classgroup",
            "subject",
            "teacher",
            "subjectrequirement",
            "teacherdailyavailability",
        ]:
            with self.subTest(model=model):
                response = self.client.get(reverse(f"admin:school_{model}_changelist"), follow=True)
                self.assertEqual(response.status_code, 200)
        for model in ["teacher", "classgroup"]:
            with self.subTest(add=model):
                response = self.client.get(reverse(f"admin:school_{model}_add"))
                self.assertEqual(response.status_code, 200)

    def test_settings_changelist_redirects_to_singleton(self):
        response = self.client.get(reverse("admin:school_schoolsettings_changelist"))
        self.assertRedirects(response, reverse("admin:school_schoolsettings_change", args=[1]))

    def test_timetable_entries_are_read_only_in_admin(self):
        response = self.client.get(reverse("admin:scheduling_timetableentry_add"))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("admin:scheduling_timetableentry_changelist"))
        self.assertEqual(response.status_code, 200)


class FillAvailabilityActionTests(AdminTestCase):
    url = reverse("admin:school_teacher_changelist")

    def test_shows_intermediate_form(self):
        teacher = make_teacher()
        response = self.client.post(
            self.url, {"action": "fill_availability", ACTION_CHECKBOX_NAME: [teacher.pk]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/action_form.html")

    def test_fills_all_operating_days_and_caps_short_days(self):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)
        teacher = make_teacher(capacity={Day.MONDAY: 3})
        self.client.post(
            self.url,
            {
                "action": "fill_availability",
                ACTION_CHECKBOX_NAME: [teacher.pk],
                "max_periods": 8,
                "apply": "Apply",
            },
        )
        capacities = dict(teacher.daily_availability.values_list("day", "max_periods"))
        self.assertEqual(capacities[Day.MONDAY], 3)  # not overwritten
        self.assertEqual(capacities[Day.TUESDAY], 8)
        self.assertEqual(capacities[Day.SATURDAY], 6)  # capped to the short day
        self.assertNotIn(Day.SUNDAY, capacities)

    def test_overwrite(self):
        teacher = make_teacher(capacity={Day.MONDAY: 3})
        self.client.post(
            self.url,
            {
                "action": "fill_availability",
                ACTION_CHECKBOX_NAME: [teacher.pk],
                "max_periods": 5,
                "overwrite": "on",
                "apply": "Apply",
            },
        )
        self.assertEqual(teacher.capacity_for_day(Day.MONDAY), 5)


class CopyTeacherConfigurationTests(AdminTestCase):
    def test_copies_subjects_and_capacities(self):
        maths, physics = make_subject("Mathematics"), make_subject("Physics")
        source = make_teacher("Source", subjects=[maths, physics], capacity=5)
        target = make_teacher("Target", subjects=[maths])
        self.client.post(
            reverse("admin:school_teacher_changelist"),
            {
                "action": "copy_configuration",
                ACTION_CHECKBOX_NAME: [target.pk],
                "source": source.pk,
                "copy_subjects": "on",
                "copy_availability": "on",
                "apply": "Apply",
            },
        )
        self.assertEqual(set(target.subjects.all()), {maths, physics})
        self.assertEqual(target.daily_availability.count(), 6)
        self.assertEqual(target.capacity_for_day(Day.WEDNESDAY), 5)


class CopyRequirementsTests(AdminTestCase):
    def test_copies_requirements_without_teachers_by_default(self):
        maths, physics = make_subject("Mathematics"), make_subject("Physics")
        teacher = make_teacher(subjects=[maths])
        source = make_class("10", "A")
        target = make_class("10", "B")
        make_requirement(source, maths, periods=6, teacher=teacher)
        make_requirement(source, physics, periods=5)
        make_requirement(target, physics, periods=3)

        self.client.post(
            reverse("admin:school_classgroup_changelist"),
            {
                "action": "copy_requirements",
                ACTION_CHECKBOX_NAME: [target.pk],
                "source": source.pk,
                "apply": "Apply",
            },
        )
        reqs = {r.subject: r for r in target.subject_requirements.all()}
        self.assertEqual(reqs[maths].periods_per_week, 6)
        self.assertIsNone(reqs[maths].teacher)
        self.assertEqual(reqs[physics].periods_per_week, 3)  # existing kept
        self.assertEqual(SubjectRequirement.objects.filter(class_group=source).count(), 2)


class TeacherSubjectRemovalGuardTests(AdminTestCase):
    def test_cannot_remove_subject_teacher_is_designated_for(self):
        maths = make_subject("Mathematics")
        teacher = make_teacher(subjects=[maths])
        make_requirement(make_class(), maths, periods=6, teacher=teacher)
        link = TeacherSubject.objects.get(teacher=teacher)

        url = reverse("admin:school_teacher_change", args=[teacher.pk])
        data = {
            "name": teacher.name,
            "is_active": "on",
            "teacher_subjects-TOTAL_FORMS": "1",
            "teacher_subjects-INITIAL_FORMS": "1",
            "teacher_subjects-0-id": link.pk,
            "teacher_subjects-0-teacher": teacher.pk,
            "teacher_subjects-0-subject": maths.pk,
            "teacher_subjects-0-DELETE": "on",
            "daily_availability-TOTAL_FORMS": "0",
            "daily_availability-INITIAL_FORMS": "0",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)  # form redisplayed with error
        self.assertContains(response, "Cannot remove Mathematics")
        self.assertTrue(TeacherSubject.objects.filter(pk=link.pk).exists())

    def test_daily_capacity_inline_validates_against_day_length(self):
        teacher = make_teacher()
        url = reverse("admin:school_teacher_change", args=[teacher.pk])
        data = {
            "name": teacher.name,
            "is_active": "on",
            "teacher_subjects-TOTAL_FORMS": "0",
            "teacher_subjects-INITIAL_FORMS": "0",
            "daily_availability-TOTAL_FORMS": "1",
            "daily_availability-INITIAL_FORMS": "0",
            "daily_availability-0-day": Day.MONDAY,
            "daily_availability-0-max_periods": 11,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeacherDailyAvailability.objects.filter(teacher=teacher).exists())
