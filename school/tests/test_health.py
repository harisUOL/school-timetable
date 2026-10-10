from django.test import TestCase, override_settings

from school.constants import Day
from school.health import group_issues, run_health_checks
from school.models import (
    SchoolDayConfiguration,
    SubjectRequirement,
    TeacherDailyAvailability,
    TeacherSubject,
)

from .factories import make_class, make_requirement, make_subject, make_teacher


def codes():
    return {issue.code for issue in run_health_checks()}


class HealthCheckTests(TestCase):
    def setUp(self):
        self.class_group = make_class("10", "A")
        self.maths = make_subject("Mathematics", "MATH")
        self.teacher = make_teacher("Ms Maths", subjects=[self.maths], capacity=5)

    def test_clean_configuration_has_no_issues(self):
        make_requirement(self.class_group, self.maths, periods=5, teacher=self.teacher)
        self.assertEqual(run_health_checks(), [])

    def test_requirement_without_qualified_active_teacher(self):
        physics = make_subject("Physics")
        requirement = make_requirement(self.class_group, physics, periods=3)
        self.assertIn("NO_QUALIFIED_TEACHER", codes())
        inactive = make_teacher("Inactive", subjects=[physics], capacity=5, is_active=False)
        self.assertIn("NO_QUALIFIED_TEACHER", codes())
        inactive.is_active = True
        inactive.save()
        self.assertNotIn("NO_QUALIFIED_TEACHER", codes())
        self.assertTrue(requirement.pk)

    def test_designated_teacher_unqualified(self):
        requirement = make_requirement(self.class_group, self.maths, periods=5, teacher=self.teacher)
        TeacherSubject.objects.filter(teacher=self.teacher).delete()
        make_teacher("Backup", subjects=[self.maths], capacity=5)
        issues = run_health_checks()
        self.assertIn("DESIGNATED_UNQUALIFIED", {i.code for i in issues})
        self.assertIn("Ms Maths", next(i for i in issues if i.code == "DESIGNATED_UNQUALIFIED").message)
        TeacherSubject.objects.create(teacher=self.teacher, subject=self.maths)
        self.assertNotIn("DESIGNATED_UNQUALIFIED", codes())
        self.assertTrue(requirement.pk)

    def test_designated_teacher_inactive(self):
        make_requirement(self.class_group, self.maths, periods=5, teacher=self.teacher)
        make_teacher("Backup", subjects=[self.maths], capacity=5)
        self.teacher.is_active = False
        self.teacher.save()
        self.assertIn("DESIGNATED_INACTIVE", codes())
        self.teacher.is_active = True
        self.teacher.save()
        self.assertNotIn("DESIGNATED_INACTIVE", codes())

    def test_class_over_weekly_slots(self):
        big = make_teacher("Big", subjects=[self.maths], capacity=9)
        make_requirement(self.class_group, self.maths, periods=50, teacher=big)
        other = make_subject("Physics")
        make_teacher("P", subjects=[other], capacity=9)
        requirement = make_requirement(self.class_group, other, periods=5)
        self.assertIn("CLASS_OVER_SLOTS", codes())
        requirement.periods_per_week = 4
        requirement.save()
        self.assertNotIn("CLASS_OVER_SLOTS", codes())

    def test_teacher_designated_load_over_capacity(self):
        # 5 a day for 6 days = 30 a week.
        make_requirement(self.class_group, self.maths, periods=20, teacher=self.teacher)
        requirement = make_requirement(make_class("10", "B"), self.maths, periods=12, teacher=self.teacher)
        self.assertIn("TEACHER_OVERLOADED", codes())
        requirement.periods_per_week = 10
        requirement.save()
        self.assertNotIn("TEACHER_OVERLOADED", codes())

    @override_settings(TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY=2)
    def test_designated_lessons_on_a_class_cannot_fit_daily_capacities(self):
        # Available Monday and Tuesday only: at most 2 Maths lessons a day = 4.
        teacher = make_teacher("Two Days", subjects=[self.maths], capacity={Day.MONDAY: 5, Day.TUESDAY: 5})
        requirement = make_requirement(self.class_group, self.maths, periods=5, teacher=teacher)
        issues = [i for i in run_health_checks() if i.code == "CLASS_LOAD_UNFITTABLE"]
        self.assertEqual(len(issues), 1)
        self.assertIn("10A", issues[0].message)
        requirement.periods_per_week = 4
        requirement.save()
        self.assertNotIn("CLASS_LOAD_UNFITTABLE", codes())

    def test_subject_demand_exceeds_qualified_capacity(self):
        # 30 a week of supply.
        make_requirement(self.class_group, self.maths, periods=20)
        requirement = make_requirement(make_class("10", "B"), self.maths, periods=20)
        self.assertIn("SUBJECT_UNDERSUPPLIED", codes())
        requirement.periods_per_week = 10
        requirement.save()
        self.assertNotIn("SUBJECT_UNDERSUPPLIED", codes())

    def test_active_teacher_without_capacity_records(self):
        teacher = make_teacher("No Days", subjects=[self.maths])
        self.assertIn("NO_CAPACITY_RECORDS", codes())
        TeacherDailyAvailability.objects.create(teacher=teacher, day=Day.MONDAY, max_periods=3)
        self.assertNotIn("NO_CAPACITY_RECORDS", codes())

    def test_capacity_record_above_day_length(self):
        # Records are validated on save through forms; this simulates a later change.
        TeacherDailyAvailability.objects.filter(teacher=self.teacher, day=Day.SATURDAY).update(max_periods=8)
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)
        self.assertIn("CAPACITY_ABOVE_DAY_LENGTH", codes())
        TeacherDailyAvailability.objects.filter(teacher=self.teacher, day=Day.SATURDAY).update(max_periods=6)
        self.assertNotIn("CAPACITY_ABOVE_DAY_LENGTH", codes())

    def test_issues_are_grouped_errors_first_with_fix_links(self):
        make_teacher("No Days", subjects=[self.maths])
        make_requirement(self.class_group, make_subject("Physics"), periods=3)
        issues = run_health_checks()
        self.assertEqual([i.severity for i in issues], ["error", "warning"])
        self.assertTrue(all(i.fix_url.startswith("/admin/") for i in issues))
        groups = group_issues(issues)
        self.assertEqual([g.code for g in groups], ["NO_QUALIFIED_TEACHER", "NO_CAPACITY_RECORDS"])

    def test_requirement_rows_are_untouched(self):
        make_requirement(self.class_group, self.maths, periods=5, teacher=self.teacher)
        run_health_checks()
        self.assertEqual(SubjectRequirement.objects.get().periods_per_week, 5)
