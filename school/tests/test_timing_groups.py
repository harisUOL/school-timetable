"""
Timing groups: grades with their own bell times, and teacher clashes by clock time.

The fixture mirrors the school's real problem in miniature: Seniors (grade 10)
start at 07:30 and Juniors (grade 1) at 09:00, both with 40-minute periods,
and Mr Shared teaches in both. Senior period 3 (08:50–09:30) overlaps Junior
period 1 (09:00–09:40), while Senior period 1 (07:30–08:10) does not.
"""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from generator import services as generator_services
from generator.model import load_problem, solve
from school.constants import Day
from school.day_schedule import Calendar, get_class_week, overlaps
from school.models import Grade, SchoolDayConfiguration, SubjectRequirement, TeacherDailyAvailability, TimingGroup, TimingGroupDay
from scheduling import selectors, services
from scheduling import validators as v
from scheduling.models import TimetableEntry

from .factories import make_class, make_requirement, make_subject, make_teacher, make_user


class TwoGroupSchool(TestCase):
    def setUp(self):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(is_operating=False)
        self.senior = make_class("10", "A")
        self.junior = make_class("1", "A", order=1)
        self.seniors = TimingGroup.objects.create(name="Seniors", order=2)
        self.juniors = TimingGroup.objects.create(name="Juniors", order=1)
        Grade.objects.filter(name="10").update(timing_group=self.seniors)
        Grade.objects.filter(name="1").update(timing_group=self.juniors)
        for day in range(Day.MONDAY, Day.SATURDAY):
            TimingGroupDay.objects.create(group=self.seniors, day=day, start_time=datetime.time(7, 30))
            TimingGroupDay.objects.create(
                group=self.juniors, day=day, start_time=datetime.time(9, 0), periods=6, recess_after_period=3
            )
        self.maths = make_subject("Mathematics", "MAT")
        self.english = make_subject("English", "ENG")
        self.shared = make_teacher("Mr Shared", subjects=[self.maths, self.english], capacity=9)
        self.other = make_teacher("Ms Other", subjects=[self.maths, self.english], capacity=9)
        self.editor = make_user("editor", superuser=True)

    def assign(self, class_group, period, teacher=None, subject=None, day=Day.MONDAY):
        return services.assign_lesson(
            class_group=class_group,
            day=day,
            period=period,
            subject=subject or self.maths,
            teacher=teacher or self.shared,
            user=self.editor,
        )


class CalendarTests(TwoGroupSchool):
    def test_each_class_follows_its_group(self):
        calendar = Calendar.load()
        senior_monday = calendar.class_day(self.senior.pk, Day.MONDAY)
        junior_monday = calendar.class_day(self.junior.pk, Day.MONDAY)
        self.assertEqual(senior_monday.period_label(1), "07:30–08:10")
        self.assertEqual(senior_monday.periods, 9)  # inherited from the school week
        self.assertEqual(junior_monday.period_label(1), "09:00–09:40")
        self.assertEqual(junior_monday.periods, 6)
        self.assertEqual(junior_monday.recess_time.label, "11:00–11:20")
        self.assertEqual(calendar.class_weekly_slots(self.junior.pk), 30)
        self.assertEqual(calendar.max_periods(Day.MONDAY), 9)

    def test_a_grade_without_a_group_follows_the_school_week(self):
        other = make_class("5", "A")
        self.assertEqual(get_class_week(other)[0].period_label(1), "08:00–08:40")

    def test_overlap_is_by_clock_time(self):
        calendar = Calendar.load()
        junior_p1 = calendar.interval(self.junior.pk, Day.MONDAY, 1)
        self.assertTrue(overlaps(calendar.interval(self.senior.pk, Day.MONDAY, 3), junior_p1))
        self.assertFalse(overlaps(calendar.interval(self.senior.pk, Day.MONDAY, 1), junior_p1))

    def test_requirement_is_capped_by_the_class_week(self):
        requirement = SubjectRequirement(class_group=self.junior, subject=self.maths, periods_per_week=31)
        with self.assertRaisesMessage(ValidationError, "1A has only 30 teaching periods per week."):
            requirement.full_clean()

    def test_group_day_validation(self):
        row = TimingGroupDay(group=self.juniors, day=Day.TUESDAY, periods=3, recess_after_period=3)
        with self.assertRaisesMessage(ValidationError, "Juniors, Tuesday: recess after period 3 is not possible"):
            row.full_clean()


class ClashTests(TwoGroupSchool):
    def setUp(self):
        super().setUp()
        make_requirement(self.senior, self.maths, periods=5)
        make_requirement(self.junior, self.maths, periods=5)

    def test_overlapping_lessons_in_different_groups_clash(self):
        self.assign(self.senior, 3)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(self.junior, 1)
        violation = exc.exception.violations[0]
        self.assertEqual(violation.code, v.TEACHER_CLASH)
        self.assertEqual(violation.message, "Mr Shared is already teaching 10A on Monday, period 3 (08:50–09:30).")

    def test_same_period_number_at_different_times_is_allowed(self):
        self.assign(self.senior, 1)
        self.assign(self.junior, 1)
        self.assertEqual(TimetableEntry.objects.filter(teacher=self.shared).count(), 2)

    def test_slot_options_show_the_time_and_the_clash(self):
        self.assign(self.senior, 3)
        options = selectors.slot_options(self.junior, Day.MONDAY, 1)
        self.assertEqual(options["period"]["time"], "09:00–09:40")
        teacher = next(t for t in options["subjects"][0]["teachers"] if t["name"] == "Mr Shared")
        self.assertEqual(teacher["status"], "blocked")

    def test_changing_bell_times_reports_new_overlaps_as_conflicts(self):
        self.assign(self.senior, 1)
        self.assign(self.junior, 1)
        TimingGroupDay.objects.filter(group=self.juniors, day=Day.MONDAY).update(start_time=datetime.time(7, 30))
        groups = selectors.find_conflicts()
        self.assertEqual([g.code for g in groups], [v.TEACHER_CLASH])

    def test_class_grid_shows_its_own_bells(self):
        grid = selectors.class_grid(self.junior)
        periods = [c for c in grid.columns if c["kind"] == "period"]
        self.assertEqual(len(periods), 6)
        self.assertEqual(periods[0]["time"], "09:00–09:40")


class TeacherGridTests(TwoGroupSchool):
    def setUp(self):
        super().setUp()
        make_requirement(self.senior, self.maths, periods=5)
        make_requirement(self.junior, self.maths, periods=5)

    def test_one_group_gives_a_normal_grid(self):
        self.assign(self.senior, 2)
        grid = selectors.teacher_grid(self.shared)
        self.assertEqual(grid.note, "")
        self.assertEqual(grid.columns[0]["time"], "07:30–08:10")

    def test_teacher_across_groups_gets_lessons_in_time_order(self):
        self.assign(self.junior, 1)  # 09:00
        self.assign(self.senior, 1)  # 07:30
        grid = selectors.teacher_grid(self.shared)
        self.assertEqual(grid.note, selectors.MIXED_NOTE)
        monday = grid.rows[0].cells
        self.assertEqual([c.entry.class_group for c in monday[:2]], [self.senior, self.junior])
        self.assertEqual([c.time_label for c in monday[:2]], ["07:30–08:10", "09:00–09:40"])
        self.client.force_login(self.editor)
        response = self.client.get(reverse("scheduling:teacher_timetable", args=[self.shared.pk]))
        self.assertContains(response, "different bell times")


class GeneratorTests(TwoGroupSchool):
    def setUp(self):
        super().setUp()
        # Mr Shared must teach both classes a lot: overlaps are the only thing to avoid.
        make_requirement(self.senior, self.maths, periods=12, teacher=self.shared)
        make_requirement(self.junior, self.maths, periods=12, teacher=self.shared)
        make_requirement(self.senior, self.english, periods=5, teacher=self.other)
        make_requirement(self.junior, self.english, periods=5, teacher=self.other)

    def test_solution_never_overlaps_a_teacher(self):
        problem = load_problem(keep_locked_only=False)
        solution = solve(problem, time_limit=10)
        self.assertTrue(solution.found, solution.status)
        calendar = problem.calendar
        by_teacher_day = {}
        for lesson in solution.lessons:
            interval = calendar.interval(lesson.class_id, lesson.day, lesson.period)
            for other in by_teacher_day.setdefault((lesson.teacher_id, lesson.day), []):
                self.assertFalse(overlaps(interval, other), (lesson, interval, other))
            by_teacher_day[(lesson.teacher_id, lesson.day)].append(interval)
        self.assertEqual(len(solution.lessons), 34)

    def test_generated_timetable_applies_cleanly(self):
        run = generator_services.run_generation(time_limit=10)
        self.assertEqual(run.status, "succeeded", run.reasons)
        self.assertEqual(generator_services.validate_proposal(run), [])
        generator_services.apply_run(run, self.editor)
        self.assertEqual(TimetableEntry.objects.count(), 34)
        self.assertEqual(selectors.find_conflicts(), [])


class TimingGroupPageTests(TwoGroupSchool):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.editor)
        self.five = make_class("5", "B", order=5)

    def payload(self, name="Middle", grades=(), **days):
        data = {"group-name": name, "group-order": "3", "fit_capacities": "on"}
        if grades:
            data["group-grades"] = [str(g) for g in grades]
        for day in range(Day.MONDAY, Day.SATURDAY):
            for field in ["periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]:
                data[f"day-{day}-{field}"] = ""
        data.update(days)
        return data

    def test_settings_page_lists_groups_and_their_bells(self):
        response = self.client.get(reverse("school:settings"))
        self.assertContains(response, "Juniors")
        self.assertContains(response, "09:00–09:40")
        self.assertContains(response, reverse("school:timing_group_edit", args=[self.juniors.pk]))

    def test_create_a_group(self):
        grade = self.five.grade
        response = self.client.post(
            reverse("school:timing_group_new"),
            self.payload(grades=[grade.pk], **{"day-4-start_time": "07:00", "day-4-period_minutes": "45"}),
        )
        self.assertRedirects(response, reverse("school:settings"))
        group = TimingGroup.objects.get(name="Middle")
        self.assertEqual(list(group.grades.all()), [grade])
        friday = get_class_week(self.five)[4]
        self.assertEqual(friday.period_label(1), "07:00–07:45")
        self.assertEqual(get_class_week(self.five)[0].period_label(1), "08:00–08:40")  # inherited
        self.assertEqual(group.days.count(), 1)

    def test_moving_a_grade_that_would_strand_lessons_is_refused(self):
        make_requirement(self.five, self.maths, periods=5)
        services.assign_lesson(
            class_group=self.five, day=Day.MONDAY, period=8, subject=self.maths, teacher=self.other, user=self.editor
        )
        response = self.client.post(
            reverse("school:timing_group_edit", args=[self.juniors.pk]),
            self.payload(
                name="Juniors", grades=[self.junior.grade.pk, self.five.grade.pk], **{"day-0-periods": "6", "day-0-recess_after_period": "3"}
            ),
        )
        self.assertContains(
            response, "Monday: 1 timetable lesson(s) are scheduled beyond period 6 for 5B", status_code=400
        )
        self.assertIsNone(Grade.objects.get(pk=self.five.grade.pk).timing_group)

    def test_duplicate_name_is_refused(self):
        response = self.client.post(reverse("school:timing_group_new"), self.payload(name="juniors"))
        self.assertContains(response, "There is already a timing group called", status_code=400)

    def test_delete_a_group(self):
        response = self.client.post(reverse("school:timing_group_delete", args=[self.juniors.pk]))
        self.assertRedirects(response, reverse("school:settings"))
        self.assertFalse(TimingGroup.objects.filter(name="Juniors").exists())
        self.assertEqual(get_class_week(self.junior)[0].periods, 9)

    def test_delete_is_refused_when_lessons_would_fall_outside_the_day(self):
        TimingGroupDay.objects.filter(group=self.seniors, day=Day.MONDAY).update(periods=11)
        make_requirement(self.senior, self.maths, periods=5)
        services.assign_lesson(
            class_group=self.senior, day=Day.MONDAY, period=11, subject=self.maths, teacher=self.other, user=self.editor
        )
        response = self.client.post(reverse("school:timing_group_delete", args=[self.seniors.pk]))
        self.assertRedirects(response, reverse("school:timing_group_edit", args=[self.seniors.pk]))
        self.assertTrue(TimingGroup.objects.filter(pk=self.seniors.pk).exists())

    def test_longer_group_day_lets_full_day_capacities_follow(self):
        TeacherDailyAvailability.objects.filter(day=Day.MONDAY).update(max_periods=9)
        response = self.client.post(
            reverse("school:timing_group_edit", args=[self.seniors.pk]),
            self.payload(name="Seniors", grades=[self.senior.grade.pk], **{"day-0-periods": "10"}),
        )
        self.assertRedirects(response, reverse("school:settings"))
        self.assertEqual(
            set(TeacherDailyAvailability.objects.filter(day=Day.MONDAY).values_list("max_periods", flat=True)), {10}
        )
