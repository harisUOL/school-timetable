"""Activities: named blocks (assembly, activity session) that hold slots but are not lessons."""

import json

from django.urls import reverse

from exports import tables
from generator.model import load_problem, solve
from school.constants import Day
from school.health import run_health_checks
from school.models import SchoolDayConfiguration
from school.tests.factories import make_class
from scheduling import selectors, services
from scheduling import validators as v
from scheduling.models import Activity, TimetableAuditLog, TimetableEntry

from .fixtures import SchoolFixture


def codes(exc):
    return [x.code for x in exc.exception.violations]


class ActivityServiceTests(SchoolFixture):
    def add(self, class_group=None, scope="class", start=5, length=2, **kwargs):
        return services.add_activity(
            class_group=class_group or self.c10a,
            day=Day.SATURDAY,
            start=start,
            length=length,
            label=kwargs.pop("label", "Activity session"),
            scope=scope,
            user=self.editor,
            **kwargs,
        )

    def test_add_two_periods_for_one_class(self):
        result = self.add()
        self.assertEqual(result.periods, [5, 6])
        self.assertEqual(
            list(Activity.objects.values_list("class_group__section__name", "period", "label")),
            [("A", 5, "Activity session"), ("A", 6, "Activity session")],
        )
        self.assertEqual(TimetableAuditLog.objects.filter(action="activity_add").count(), 1)

    def test_grade_scope_adds_it_to_every_class_in_the_grade(self):
        result = self.add(scope="grade")
        self.assertEqual({c.pk for c in result.classes}, {self.c10a.pk, self.c10b.pk})
        self.assertEqual(Activity.objects.count(), 4)
        self.assertEqual(Activity.objects.values("batch").distinct().count(), 1)

    def test_school_scope_skips_classes_whose_day_is_too_short(self):
        short = make_class("1", "A", order=1)
        from school.models import Grade, TimingGroup, TimingGroupDay

        group = TimingGroup.objects.create(name="Juniors")
        Grade.objects.filter(pk=short.grade_id).update(timing_group=group)
        TimingGroupDay.objects.create(group=group, day=Day.SATURDAY, periods=4, recess_after_period=2)
        result = self.add(scope="school")
        self.assertEqual(result.skipped, [short])
        self.assertFalse(Activity.objects.filter(class_group=short).exists())

    def test_lessons_block_it_unless_cleared(self):
        self.assign(day=Day.SATURDAY, period=6)
        with self.assertRaises(services.SchedulingError) as exc:
            self.add()
        self.assertEqual(codes(exc), [v.SLOT_OCCUPIED])
        self.assertIn("1 lesson(s) are already in those periods (10A MATH)", str(exc.exception))
        self.assertFalse(Activity.objects.exists())
        result = self.add(clear_lessons=True)
        self.assertEqual(result.cleared, 1)
        self.assertFalse(TimetableEntry.objects.exists())
        self.assertTrue(TimetableAuditLog.objects.filter(action="clear", period=6).exists())

    def test_locked_lessons_always_block_it(self):
        entry = self.assign(day=Day.SATURDAY, period=5).entry
        services.set_locked(entry=entry, locked=True, user=self.editor)
        with self.assertRaises(services.SchedulingError) as exc:
            self.add(clear_lessons=True)
        self.assertIn("locked lesson", str(exc.exception))
        self.assertTrue(TimetableEntry.objects.filter(pk=entry.pk).exists())

    def test_overlapping_activity_and_bad_input_are_refused(self):
        self.add()
        with self.assertRaises(services.SchedulingError) as exc:
            self.add(start=6, length=1, label="Assembly")
        self.assertIn("10A already has Activity session on Saturday, period 6", str(exc.exception))
        with self.assertRaises(services.SchedulingError) as exc:
            self.add(label="   ")
        self.assertEqual(codes(exc), ["ACTIVITY_NAME"])
        with self.assertRaises(services.SchedulingError) as exc:
            self.add(start=6, length=2)  # Saturday has 6 periods
        self.assertEqual(codes(exc), [v.INVALID_SLOT])

    def test_a_lesson_cannot_go_into_an_activity(self):
        self.add()
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(day=Day.SATURDAY, period=5)
        self.assertEqual(codes(exc), [v.SLOT_OCCUPIED])
        self.assertIn("10A has Activity session on Saturday, period 5", str(exc.exception))

    def test_remove_from_one_class_or_everywhere(self):
        self.add(scope="grade")
        result = services.remove_activity(class_group=self.c10a, day=Day.SATURDAY, period=6, user=self.editor)
        self.assertEqual(result.classes, [self.c10a])
        self.assertEqual(set(Activity.objects.values_list("class_group", flat=True)), {self.c10b.pk})
        services.remove_activity(class_group=self.c10b, day=Day.SATURDAY, period=5, everywhere=True, user=self.editor)
        self.assertFalse(Activity.objects.exists())
        with self.assertRaises(services.SchedulingError):
            services.remove_activity(class_group=self.c10b, day=Day.SATURDAY, period=5, user=self.editor)


class ActivityDisplayTests(ActivityServiceTests):
    def test_grid_shows_one_joined_block(self):
        self.add()
        saturday = selectors.class_grid(self.c10a).rows[-1]
        cells = [c for c in saturday.cells if c.kind == selectors.ACTIVITY]
        self.assertEqual([(c.period, c.activity, c.joins) for c in cells], [(5, "Activity session", "start"), (6, "Activity session", "end")])
        self.assertEqual(selectors.class_grid(self.c10a).filled, 2)

    def test_exports_and_slot_options(self):
        self.add()
        table = tables.class_table(self.c10a)
        self.assertIn("Activity session", table.rows[-1])
        options = selectors.slot_options(self.c10a, Day.SATURDAY, 6)
        self.assertEqual(options["activity"]["span"], "periods 5–6")
        self.assertEqual(options["subjects"], [])
        empty = selectors.slot_options(self.c10a, Day.SATURDAY, 2)
        self.assertEqual(empty["activity_form"]["max_length"], 5)
        self.assertEqual([s["value"] for s in empty["activity_form"]["scopes"]], ["class", "grade"])

    def test_generator_leaves_activity_slots_free(self):
        self.add(scope="grade", start=1, length=6)
        solution = solve(load_problem(keep_locked_only=False), time_limit=5)
        self.assertTrue(solution.found)
        self.assertFalse([l for l in solution.lessons if l.day == Day.SATURDAY])

    def test_health_counts_activity_periods_as_taken(self):
        self.add(scope="class", start=1, length=6)
        for day in (Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY, Day.THURSDAY):
            SchoolDayConfiguration.objects.filter(day=day).update(is_operating=False)
        # 10A now has Friday's 9 periods (Saturday is all activity) for 8 required lessons.
        self.assertFalse([i for i in run_health_checks() if i.code == "CLASS_OVER_SLOTS"])
        services.remove_activity(class_group=self.c10a, day=Day.SATURDAY, period=1, user=self.editor)
        self.add(scope="class", start=1, length=6, label="Sports day")
        SchoolDayConfiguration.objects.filter(day=Day.FRIDAY).update(periods=3)
        issues = [i.message for i in run_health_checks() if i.code == "CLASS_OVER_SLOTS"]
        self.assertEqual(issues, ["10A requires 8 periods a week but has only 3 teaching slots (6 are taken by activities)."])


class ActivityApiTests(SchoolFixture):
    def post(self, name, **data):
        body = {"class_id": self.c10a.pk, "day": Day.SATURDAY, "period": 5, **data}
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_add_and_remove_through_the_api(self):
        self.client.force_login(self.editor)
        response = self.post("scheduling:add_activity", label="Activity session", length=2, scope="grade", clear_lessons=False)
        self.assertEqual(response.json(), {"ok": True, "reload": True})
        self.assertEqual(Activity.objects.count(), 4)
        page = self.client.get(reverse("scheduling:class_timetable", args=[self.c10a.pk]))
        self.assertContains(page, "Added Activity session on Saturday, period 5–6 for 2 classes.")
        self.assertContains(page, "slot-activity joins-start")
        response = self.post("scheduling:remove_activity", everywhere=True)
        self.assertEqual(response.json()["ok"], True)
        self.assertFalse(Activity.objects.exists())

    def test_rule_violations_and_bad_input(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.post("scheduling:add_activity", label="", length=1).status_code, 400)
        self.assertEqual(self.post("scheduling:add_activity", label="X", length=1, scope="galaxy").status_code, 400)
        self.assertEqual(self.post("scheduling:remove_activity").status_code, 400)

    def test_viewer_cannot_add(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.post("scheduling:add_activity", label="X", length=1).status_code, 403)
        self.assertFalse(Activity.objects.exists())
