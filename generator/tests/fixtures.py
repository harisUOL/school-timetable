from collections import Counter

from django.test import TestCase, override_settings

from school.constants import Day
from school.models import SchoolDayConfiguration
from school.tests.factories import make_class, make_requirement, make_subject, make_teacher, make_user
from scheduling.models import TimetableEntry

FAST = override_settings(
    GENERATOR={
        "DEFAULT_TIME_LIMIT": 10,
        "MAX_TIME_LIMIT": 600,
        "WORKERS": 4,
        "MAX_CONSECUTIVE": 3,
        "WEIGHTS": {
            "unplaced_lesson": 1000,
            "subject_daily_excess": 40,
            "subject_spread_day": 6,
            "teacher_consecutive": 8,
            "teacher_gap": 3,
            "teacher_balance": 1,
        },
    }
)


@FAST
class SmallSchool(TestCase):
    """
    Three days (Mon–Wed) of 4 periods, recess after 2: 12 slots per class.

    10A: Maths 4 (Ms Maths), English 4 (Mr English), Science 3 (open)
    10B: Maths 4 (open),     English 4 (Mr English), Science 3 (Dr Science)
    """

    @classmethod
    def setUpTestData(cls):
        SchoolDayConfiguration.objects.filter(day__in=[Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY]).update(
            periods=4, recess_after_period=2
        )
        SchoolDayConfiguration.objects.filter(day__gt=Day.WEDNESDAY).update(is_operating=False)
        days3 = lambda n: {Day.MONDAY: n, Day.TUESDAY: n, Day.WEDNESDAY: n}  # noqa: E731
        cls.c10a, cls.c10b = make_class("10", "A"), make_class("10", "B")
        cls.maths, cls.english, cls.science = (
            make_subject("Mathematics", "MATH"),
            make_subject("English", "ENG"),
            make_subject("Science", "SCI"),
        )
        cls.ms_maths = make_teacher("Ms Maths", subjects=[cls.maths], capacity=days3(3))
        cls.mr_english = make_teacher("Mr English", subjects=[cls.english], capacity=days3(3))
        cls.dr_science = make_teacher("Dr Science", subjects=[cls.science, cls.maths], capacity=days3(3))
        cls.ms_cover = make_teacher("Ms Cover", subjects=[cls.science, cls.maths], capacity=days3(2), is_cover_teacher=True)
        make_requirement(cls.c10a, cls.maths, 4, cls.ms_maths)
        make_requirement(cls.c10a, cls.english, 4, cls.mr_english)
        cls.req_10a_science = make_requirement(cls.c10a, cls.science, 3)
        cls.req_10b_maths = make_requirement(cls.c10b, cls.maths, 4)
        make_requirement(cls.c10b, cls.english, 4, cls.mr_english)
        make_requirement(cls.c10b, cls.science, 3, cls.dr_science)
        cls.editor = make_user("editor", "Timetable Editor")
        cls.viewer = make_user("viewer", "Viewer")

    def assert_daily_capacity_respected(self, lessons):
        per_day = Counter((lesson.teacher_id, lesson.day) for lesson in lessons)
        from school.models import TeacherDailyAvailability

        caps = {(t, d): m for t, d, m in TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods")}
        for (teacher_id, day), count in per_day.items():
            self.assertLessEqual(count, caps.get((teacher_id, day), 0), f"teacher {teacher_id} day {day}")

    def live(self):
        return list(TimetableEntry.objects.all())
