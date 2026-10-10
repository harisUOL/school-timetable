import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from exports import tables
from scheduling import selectors
from scheduling.models import TimetableEntry
from school.constants import Day
from school.day_schedule import DaySchedule, get_day_schedule, weekly_teaching_slots
from school.models import SchoolDayConfiguration, SchoolSettings, TeacherDailyAvailability
from school.roles import EDITOR_GROUP, VIEWER_GROUP

from .factories import make_class, make_requirement, make_subject, make_teacher, make_user


def labels(schedule):
    return [(t.kind, t.number, t.label) for t in schedule.timings]


class DayScheduleTimingTests(TestCase):
    def test_default_bell_times(self):
        monday = get_day_schedule(Day.MONDAY)
        self.assertEqual(
            labels(monday)[:6],
            [
                ("period", 1, "08:00–08:40"),
                ("period", 2, "08:40–09:20"),
                ("period", 3, "09:20–10:00"),
                ("period", 4, "10:00–10:40"),
                ("recess", 4, "10:40–11:00"),
                ("period", 5, "11:00–11:40"),
            ],
        )
        self.assertEqual(monday.hours_label, "08:00–14:20")
        self.assertEqual(monday.period_time(9).label, "13:40–14:20")
        self.assertEqual(monday.recess_time.minutes, 20)

    def test_day_overrides_inherit_the_rest(self):
        SchoolDayConfiguration.objects.filter(day=Day.FRIDAY).update(
            periods=6, start_time=datetime.time(7, 30), period_minutes=30
        )
        friday = get_day_schedule(Day.FRIDAY)
        self.assertEqual(friday.period_time(1).label, "07:30–08:00")
        self.assertEqual(friday.recess_time.label, "09:30–09:50")  # school default 20 minutes
        self.assertEqual(friday.hours_label, "07:30–10:50")

    def test_no_recess_means_no_recess_slot(self):
        schedule = DaySchedule(Day.MONDAY, 3, 0, datetime.time(9, 0), 30, 15)
        self.assertEqual([t.kind for t in schedule.timings], ["period"] * 3)
        self.assertEqual(schedule.hours_label, "09:00–10:30")

    def test_a_day_running_past_midnight_is_refused(self):
        friday = SchoolDayConfiguration.objects.get(day=Day.FRIDAY)
        friday.start_time = datetime.time(22, 0)
        with self.assertRaisesMessage(ValidationError, "run past midnight"):
            friday.full_clean()
        settings = SchoolSettings.load()
        settings.day_start_time = datetime.time(21, 0)
        with self.assertRaisesMessage(ValidationError, "Monday: 9 periods of 40 minutes from 21:00 run past midnight."):
            settings.full_clean()


class GridTimeTests(TestCase):
    def setUp(self):
        self.class_group = make_class()

    def test_header_shows_times_when_every_day_agrees(self):
        grid = selectors.class_grid(self.class_group)
        periods = [c for c in grid.columns if c["kind"] == "period"]
        self.assertEqual(periods[0]["time"], "08:00–08:40")
        recess = next(c for c in grid.columns if c["kind"] == "recess")
        self.assertEqual(recess["time"], "10:40–11:00")
        self.assertFalse(any(cell.own_time for row in grid.rows for cell in row.cells))

    def test_a_day_with_other_times_marks_its_own_cells(self):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(start_time=datetime.time(9, 0))
        grid = selectors.class_grid(self.class_group)
        first = next(c for c in grid.columns if c["kind"] == "period")
        self.assertEqual(first["time"], "08:00–08:40")  # what most days share
        saturday = next(r for r in grid.rows if r.day == Day.SATURDAY)
        self.assertEqual(saturday.cells[0].time_label, "09:00–09:40")
        self.assertTrue(saturday.cells[0].own_time)
        response_table = tables.class_table(self.class_group)
        self.assertEqual(response_table.header[1], "I\n08:00–08:40")
        self.assertTrue(response_table.rows[-1][1].startswith("09:00–09:40"))

    def test_exports_put_times_in_the_header(self):
        table = tables.class_table(self.class_group)
        self.assertEqual(table.header[1], "I\n08:00–08:40")
        self.assertEqual(table.rows[0][1], "")


class SettingsPageTests(TestCase):
    URL = reverse("school:settings")

    def setUp(self):
        self.admin = make_user("admin", superuser=True)
        self.client.force_login(self.admin)
        self.maths = make_subject()
        self.teacher = make_teacher(subjects=[self.maths], capacity=9)
        TeacherDailyAvailability.objects.filter(teacher=self.teacher, day=Day.SATURDAY).update(max_periods=6)
        TeacherDailyAvailability.objects.filter(teacher=self.teacher, day=Day.TUESDAY).update(max_periods=4)
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)

    def payload(self, **days):
        data = {
            "school-school_name": "Test School",
            "school-day_start_time": "08:00",
            "school-period_minutes": "40",
            "school-default_periods_per_day": "9",
            "school-default_recess_after_period": "4",
            "school-recess_minutes": "20",
            "fit_capacities": "on",
        }
        for day in Day.values:
            data[f"day-{day}-is_operating"] = "on" if day != Day.SUNDAY else ""
            for name in ["periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]:
                data[f"day-{day}-{name}"] = ""
        for key, value in days.items():
            data[key] = value
        return data

    def capacities(self):
        return dict(self.teacher.daily_availability.values_list("day", "max_periods"))

    def test_page_shows_bell_times_and_the_form(self):
        response = self.client.get(self.URL)
        self.assertContains(response, "08:00–14:20")
        self.assertContains(response, "Period 1</span> 08:00–08:40", html=False)
        self.assertContains(response, 'name="day-4-periods"')
        self.assertContains(response, 'name="school-day_start_time"')

    def test_swap_friday_and_saturday_lengths(self):
        response = self.client.post(self.URL, self.payload(**{"day-4-periods": "6"}))
        self.assertRedirects(response, self.URL)
        self.assertEqual(get_day_schedule(Day.FRIDAY).periods, 6)
        self.assertEqual(get_day_schedule(Day.SATURDAY).periods, 9)
        self.assertEqual(weekly_teaching_slots(), 51)
        caps = self.capacities()
        # Full days follow the day; a refined capacity (Tuesday 4) is kept.
        self.assertEqual((caps[Day.FRIDAY], caps[Day.SATURDAY], caps[Day.TUESDAY]), (6, 9, 4))
        self.assertEqual(SchoolSettings.load().school_name, "Test School")

    def test_change_bell_times(self):
        data = self.payload(
            **{
                "school-day_start_time": "07:45",
                "school-period_minutes": "35",
                "school-recess_minutes": "15",
                "day-4-period_minutes": "30",
            }
        )
        self.assertRedirects(self.client.post(self.URL, data), self.URL)
        self.assertEqual(get_day_schedule(Day.MONDAY).period_time(1).label, "07:45–08:20")
        self.assertEqual(get_day_schedule(Day.MONDAY).recess_time.label, "10:05–10:20")
        self.assertEqual(get_day_schedule(Day.FRIDAY).period_time(1).label, "07:45–08:15")
        self.assertEqual(SchoolDayConfiguration.objects.get(day=Day.FRIDAY).period_minutes, 30)

    def test_shortening_without_fitting_capacities_is_refused(self):
        data = self.payload(**{"day-4-periods": "6"})
        del data["fit_capacities"]
        response = self.client.post(self.URL, data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Friday: 1 teacher availability record(s) allow more than 6 periods", status_code=400)
        self.assertEqual(get_day_schedule(Day.FRIDAY).periods, 9)

    def test_lessons_beyond_a_shortened_day_block_the_save(self):
        class_group = make_class()
        make_requirement(class_group, self.maths, periods=5, teacher=self.teacher)
        TimetableEntry.objects.create(class_group=class_group, day=Day.FRIDAY, period=8, teacher=self.teacher, subject=self.maths)
        response = self.client.post(self.URL, self.payload(**{"day-4-periods": "6"}))
        self.assertContains(response, "Friday: 1 timetable lesson(s) or activity period(s) are scheduled beyond period 6", status_code=400)
        self.assertEqual(self.capacities()[Day.FRIDAY], 9)  # nothing changed

    def test_invalid_values_are_reported_and_nothing_saved(self):
        response = self.client.post(
            self.URL, self.payload(**{"day-4-periods": "3", "school-period_minutes": "2", "day-0-start_time": "23:00"})
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Nothing was saved", status_code=400)
        response = self.client.post(self.URL, self.payload(**{"day-0-start_time": "23:00"}))
        self.assertContains(response, "Monday: 9 periods of 40 minutes from 23:00 run past midnight.", status_code=400)
        response = self.client.post(self.URL, self.payload(**{"day-4-periods": "3"}))
        self.assertContains(response, "recess after period 4 is not possible", status_code=400)
        self.assertEqual(get_day_schedule(Day.FRIDAY).periods, 9)

    def test_closing_every_day_is_refused(self):
        data = self.payload()
        for day in Day.values:
            data[f"day-{day}-is_operating"] = ""
        self.assertContains(self.client.post(self.URL, data), "At least one day must be open.", status_code=400)

    def test_only_administrators_may_change_the_week(self):
        for username, role in [("editor", EDITOR_GROUP), ("viewer", VIEWER_GROUP)]:
            self.client.force_login(make_user(username, role))
            response = self.client.post(self.URL, self.payload(**{"day-4-periods": "6"}))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(get_day_schedule(Day.FRIDAY).periods, 9)
