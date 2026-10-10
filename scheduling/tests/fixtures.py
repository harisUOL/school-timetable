"""A small school shared by the scheduling tests."""

from django.test import TestCase

from school.constants import Day
from school.models import SchoolDayConfiguration
from school.tests.factories import make_class, make_requirement, make_subject, make_teacher, make_user
from scheduling import services


class SchoolFixture(TestCase):
    """
    10A and 10B, Saturday shortened to 6 periods.
    - Ms Maths: Mathematics, 3 lessons a day Monday–Saturday (designated for 10A Maths).
    - Mr Physics: Physics and Mathematics, 5 a day.
    - Ms Monday: Mathematics, works Monday only (2 lessons).
    - Mr Unqualified: Biology only.
    """

    @classmethod
    def setUpTestData(cls):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)
        cls.c10a = make_class("10", "A")
        cls.c10b = make_class("10", "B")
        cls.maths = make_subject("Mathematics", "MATH")
        cls.physics = make_subject("Physics", "PHY")
        cls.biology = make_subject("Biology", "BIO")
        cls.ms_maths = make_teacher("Ms Maths", subjects=[cls.maths], capacity=3)
        cls.mr_physics = make_teacher("Mr Physics", subjects=[cls.physics, cls.maths], capacity=5)
        cls.ms_monday = make_teacher("Ms Monday", subjects=[cls.maths], capacity={Day.MONDAY: 2})
        cls.mr_bio = make_teacher("Mr Unqualified", subjects=[cls.biology], capacity=5)
        cls.req_10a_maths = make_requirement(cls.c10a, cls.maths, periods=6, teacher=cls.ms_maths)
        cls.req_10a_physics = make_requirement(cls.c10a, cls.physics, periods=2)
        cls.req_10b_maths = make_requirement(cls.c10b, cls.maths, periods=6)
        cls.editor = make_user("editor", "Timetable Editor")
        cls.viewer = make_user("viewer", "Viewer")

    def assign(self, class_group=None, day=Day.MONDAY, period=1, subject=None, teacher=None, **kwargs):
        return services.assign_lesson(
            class_group=class_group or self.c10a,
            day=day,
            period=period,
            subject=subject or self.maths,
            teacher=teacher or self.ms_maths,
            user=kwargs.pop("user", self.editor),
            **kwargs,
        )
