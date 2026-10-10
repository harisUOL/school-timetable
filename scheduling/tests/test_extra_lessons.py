"""Extra lessons: beyond a subject's weekly number, or cover with a subject the class does not take."""

import json

from django.urls import reverse

from generator import services as generator_services
from generator.model import load_problem, solve
from school.constants import Day
from scheduling import selectors, services
from scheduling import validators as v
from scheduling.models import TimetableEntry

from .fixtures import SchoolFixture


def codes(exc):
    return [x.code for x in exc.exception.violations]


class ExtraLessonTests(SchoolFixture):
    def fill_physics(self):
        self.assign(period=1, subject=self.physics, teacher=self.mr_physics)
        self.assign(period=2, subject=self.physics, teacher=self.mr_physics)

    def test_a_met_subject_needs_the_extra_flag(self):
        self.fill_physics()
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(period=3, subject=self.physics, teacher=self.mr_physics)
        self.assertEqual(codes(exc), [v.REQUIREMENT_MET])
        entry = self.assign(period=3, subject=self.physics, teacher=self.mr_physics, extra=True).entry
        self.assertTrue(entry.is_extra)

    def test_cover_with_a_subject_the_class_does_not_take(self):
        entry = self.assign(period=4, subject=self.biology, teacher=self.mr_bio, extra=True).entry
        self.assertEqual((entry.subject, entry.is_extra), (self.biology, True))

    def test_every_other_rule_still_holds(self):
        self.assign(self.c10b, period=5, subject=self.maths, teacher=self.mr_physics)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(period=5, subject=self.physics, teacher=self.mr_physics, extra=True)
        self.assertEqual(codes(exc), [v.TEACHER_CLASH])
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(period=6, subject=self.biology, teacher=self.mr_physics, extra=True)
        self.assertEqual(codes(exc), [v.NOT_QUALIFIED])
        for period in (1, 2, 3):
            self.assign(period=period, teacher=self.ms_maths)
        with self.assertRaises(services.SchedulingError) as exc:
            self.assign(period=4, subject=self.maths, teacher=self.ms_maths, extra=True)
        self.assertEqual(codes(exc), [v.CAPACITY_EXCEEDED])  # an extra lesson still uses the day's capacity

    def test_extras_never_count_towards_requirements(self):
        self.assign(period=1, subject=self.physics, teacher=self.mr_physics, extra=True)
        progress = selectors.requirement_progress(self.c10a)
        physics = next(p for p in progress.subjects if p.subject == "Physics")
        self.assertEqual(physics.assigned, 0)
        self.assertEqual(progress.extras, [("Physics", 1)])
        # A regular Physics lesson can still be placed: the extra did not use the requirement up.
        self.assign(period=2, subject=self.physics, teacher=self.mr_physics)
        self.assign(period=3, subject=self.physics, teacher=self.mr_physics)
        self.assertEqual(selectors.find_conflicts(), [])
        self.assertEqual(selectors.school_completion()["assigned"], 2)

    def test_generator_keeps_extras_and_still_places_every_requirement(self):
        self.assign(period=1, subject=self.biology, teacher=self.mr_bio, extra=True)
        self.assign(self.c10b, period=1, subject=self.maths, teacher=self.mr_physics, extra=True)
        solution = solve(load_problem(keep_locked_only=False), time_limit=5)
        self.assertTrue(solution.found)
        self.assertEqual(len(solution.lessons), 6 + 2 + 6)  # the extras met none of them
        run = generator_services.run_generation(time_limit=5)
        self.assertEqual(run.status, "succeeded", run.reasons)
        self.assertEqual(generator_services.validate_proposal(run), [])
        generator_services.apply_run(run, self.editor)
        self.assertEqual(TimetableEntry.objects.filter(is_extra=True).count(), 2)
        self.assertEqual(selectors.find_conflicts(), [])

    def test_api_assigns_an_extra_lesson(self):
        self.fill_physics()
        self.client.force_login(self.editor)
        body = {
            "class_id": self.c10a.pk,
            "day": Day.MONDAY,
            "period": 3,
            "subject_id": self.physics.pk,
            "teacher_id": self.mr_physics.pk,
            "extra": True,
        }
        response = self.client.post(reverse("scheduling:assign_lesson"), json.dumps(body), content_type="application/json")
        data = response.json()
        self.assertTrue(data["ok"], data)
        self.assertIn("as an extra lesson", data["message"])
        self.assertIn("slot-extra", data["cell_html"])
        body["extra"] = "yes"
        response = self.client.post(reverse("scheduling:assign_lesson"), json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 400)
