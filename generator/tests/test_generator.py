from collections import defaultdict
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.urls import reverse

from generator import diagnose, services
from generator.model import load_problem, solve
from generator.models import GenerationRun
from school.constants import Day
from school.models import SubjectRequirement, TeacherDailyAvailability
from scheduling import services as scheduling_services
from scheduling.models import TimetableAuditLog, TimetableEntry
from school.tests.factories import make_requirement, make_teacher

from .fixtures import SmallSchool

KEEP_ALL = GenerationRun.KeepMode.KEEP_ALL
KEEP_LOCKED = GenerationRun.KeepMode.KEEP_LOCKED


class SolveTests(SmallSchool):
    def test_small_school_solves_completely_with_zero_violations(self):
        run = services.run_generation(time_limit=10)
        self.assertEqual(run.status, "succeeded", run.reasons)
        self.assertEqual(run.metrics["placed"], run.metrics["required"])
        self.assertEqual(run.metrics["unmet"], 0)
        self.assertEqual(run.lessons.count(), 22)
        self.assertEqual(services.validate_proposal(run), [])

    def test_designated_teachers_are_fixed_and_open_requirements_get_one_teacher(self):
        run = services.run_generation(time_limit=10)
        teachers = defaultdict(set)
        for lesson in run.lessons.all():
            teachers[(lesson.class_group_id, lesson.subject_id)].add(lesson.teacher_id)
        self.assertEqual(teachers[(self.c10a.pk, self.maths.pk)], {self.ms_maths.pk})
        self.assertEqual(teachers[(self.c10b.pk, self.science.pk)], {self.dr_science.pk})
        self.assertEqual(len(teachers[(self.c10a.pk, self.science.pk)]), 1)
        self.assertEqual(len(teachers[(self.c10b.pk, self.maths.pk)]), 1)

    def test_daily_capacity_never_exceeded_in_any_solution(self):
        for seed in range(4):
            with self.subTest(seed=seed):
                solution = solve(load_problem(keep_locked_only=False), time_limit=5, seed=seed)
                self.assertTrue(solution.found)
                self.assert_daily_capacity_respected(solution.lessons)
        # Tighter capacities still hold.
        TeacherDailyAvailability.objects.filter(teacher=self.mr_english, day=Day.MONDAY).update(max_periods=2)
        solution = solve(load_problem(keep_locked_only=False), time_limit=5)
        self.assertTrue(solution.found)
        self.assert_daily_capacity_respected(solution.lessons)
        self.assertLessEqual(
            sum(1 for l in solution.lessons if l.teacher_id == self.mr_english.pk and l.day == Day.MONDAY), 2
        )

class KeepModeTests(SmallSchool):
    def setUp(self):
        self.locked = scheduling_services.assign_lesson(
            class_group=self.c10a, day=Day.MONDAY, period=1, subject=self.english, teacher=self.mr_english, user=self.editor
        ).entry
        scheduling_services.set_locked(entry=self.locked, locked=True, user=self.editor)
        self.locked.refresh_from_db()
        self.unlocked = scheduling_services.assign_lesson(
            class_group=self.c10b, day=Day.TUESDAY, period=4, subject=self.english, teacher=self.mr_english, user=self.editor
        ).entry

    def test_locked_lessons_survive_keep_locked_only(self):
        run = services.run_generation(keep_mode=KEEP_LOCKED, time_limit=10)
        self.assertEqual(run.status, "succeeded", run.reasons)
        kept = run.lessons.get(is_kept=True)
        self.assertEqual(kept.source_entry_id, self.locked.pk)
        services.apply_run(run, self.editor)
        self.assertTrue(TimetableEntry.objects.filter(pk=self.locked.pk, is_locked=True, day=Day.MONDAY, period=1).exists())
        self.assertFalse(TimetableEntry.objects.filter(pk=self.unlocked.pk).exists())
        self.assertEqual(TimetableEntry.objects.count(), 22)

    def test_keep_all_existing_keeps_every_lesson(self):
        run = services.run_generation(keep_mode=KEEP_ALL, time_limit=10)
        self.assertEqual(set(run.lessons.filter(is_kept=True).values_list("source_entry_id", flat=True)), {self.locked.pk, self.unlocked.pk})
        services.apply_run(run, self.editor)
        self.assertTrue(TimetableEntry.objects.filter(pk=self.unlocked.pk).exists())
        self.assertEqual(TimetableEntry.objects.count(), 22)

    def test_kept_lessons_that_break_rules_are_refused(self):
        self.mr_english.is_active = False
        self.mr_english.save()
        make_teacher("Ms English", subjects=[self.english], capacity={Day.MONDAY: 3, Day.TUESDAY: 3, Day.WEDNESDAY: 3})
        SubjectRequirement.objects.filter(teacher=self.mr_english).update(teacher=None)
        run = services.run_generation(keep_mode=KEEP_ALL, time_limit=5)
        self.assertEqual(run.status, "refused")
        self.assertTrue(any("Mr English is inactive" in reason for reason in run.reasons))


class InfeasibleTests(SmallSchool):
    def test_health_errors_refuse_with_clear_reasons(self):
        TeacherDailyAvailability.objects.filter(teacher=self.ms_maths).update(max_periods=1)  # 3 a week < 4 designated
        run = services.run_generation(time_limit=5)
        self.assertEqual(run.status, "refused")
        self.assertIn("data-health error", run.message)
        self.assertTrue(any("Ms Maths is designated for 4 lessons a week but can teach only 3" in r for r in run.reasons))
        self.assertFalse(run.lessons.exists())

    def make_monday_only_squeeze(self):
        # 10A needs 4 + 4 lessons from two teachers who both work Monday only (4 slots).
        squeeze = {Day.MONDAY: 4}
        a = make_teacher("Mon A", subjects=[self.maths], capacity=squeeze)
        b = make_teacher("Mon B", subjects=[self.english], capacity=squeeze)
        SubjectRequirement.objects.filter(class_group=self.c10a, subject=self.maths).update(teacher=a)
        SubjectRequirement.objects.filter(class_group=self.c10a, subject=self.english).update(teacher=b)
        SubjectRequirement.objects.filter(pk=self.req_10a_science.pk).delete()

    def test_infeasible_fixture_reports_a_readable_reason(self):
        self.make_monday_only_squeeze()
        run = services.run_generation(time_limit=5)
        self.assertEqual(run.status, "infeasible")
        self.assertIn("impossible", run.message)
        self.assertTrue(
            any("10A needs 8 more lessons, but its teachers can cover only 4" in r and "Monday" in r for r in run.reasons),
            run.reasons,
        )

    def test_best_effort_places_what_it_can(self):
        self.make_monday_only_squeeze()
        run = services.run_generation(mode=GenerationRun.Mode.BEST_EFFORT, time_limit=10)
        self.assertEqual(run.status, "succeeded", run.reasons)
        self.assertEqual(run.metrics["unmet"], 4)
        self.assertTrue(any("not placed" in reason for reason in run.reasons))
        self.assertEqual(services.validate_proposal(run), [])
        self.assert_daily_capacity_respected(list(run.lessons.all()))

    def test_solver_infeasibility_is_explained(self):
        """If counting arguments pass but the pieces do not fit, the best-effort fallback names the shortfall."""
        self.make_monday_only_squeeze()
        problem = load_problem(keep_locked_only=False)
        solution = solve(problem, time_limit=5, soft=False)
        self.assertEqual(solution.status, "INFEASIBLE")
        reasons = diagnose.best_effort_shortfall(problem, time_limit=5)
        self.assertIn("lesson(s) stay unplaced", reasons[0])
        self.assertIn("10A", reasons[0])


class ApplyTests(SmallSchool):
    def test_apply_goes_through_the_service_and_writes_audit_rows(self):
        run = services.run_generation(time_limit=10)
        with mock.patch.object(scheduling_services, "assign_lesson", wraps=scheduling_services.assign_lesson) as spy:
            summary = services.apply_run(run, self.editor)
        self.assertEqual(spy.call_count, 22)
        self.assertEqual(summary["assigned"], 22)
        self.assertEqual(TimetableEntry.objects.count(), 22)
        self.assertEqual(TimetableAuditLog.objects.filter(action="assign").count(), 22)
        self.assertTrue(TimetableAuditLog.objects.filter(action="generator_apply", after__run=run.pk).exists())
        run.refresh_from_db()
        self.assertEqual(run.status, "applied")
        self.assert_daily_capacity_respected(self.live())
        with self.assertRaises(services.ApplyError):
            services.apply_run(run, self.editor)  # only once

    def test_failure_part_way_changes_nothing(self):
        run = services.run_generation(time_limit=10)
        original = scheduling_services.assign_lesson
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 10:
                raise scheduling_services.SchedulingError([scheduling_services.v.Violation("TEACHER_CLASH", "Boom.")])
            return original(**kwargs)

        with mock.patch.object(scheduling_services, "assign_lesson", side_effect=flaky):
            with self.assertRaises(services.ApplyError) as exc:
                services.apply_run(run, self.editor)
        self.assertIn("Boom.", exc.exception.problems)
        self.assertEqual(TimetableEntry.objects.count(), 0)
        self.assertEqual(TimetableAuditLog.objects.count(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, "succeeded")

    def test_stale_proposal_is_refused(self):
        run = services.run_generation(time_limit=10)
        scheduling_services.assign_lesson(
            class_group=self.c10a, day=Day.MONDAY, period=1, subject=self.english, teacher=self.mr_english, user=self.editor
        )
        with self.assertRaises(services.ApplyError) as exc:
            services.apply_run(run, self.editor)
        self.assertTrue(any("added or locked" in p for p in exc.exception.problems))
        self.assertEqual(TimetableEntry.objects.count(), 1)

    def test_configuration_change_after_run_is_caught_by_revalidation(self):
        run = services.run_generation(time_limit=10)
        TeacherDailyAvailability.objects.filter(teacher=self.mr_english).update(max_periods=1)
        with self.assertRaises(services.ApplyError) as exc:
            services.apply_run(run, self.editor)
        self.assertTrue(any("Mr English already has" in p for p in exc.exception.problems))
        self.assertEqual(TimetableEntry.objects.count(), 0)


class CommandTests(SmallSchool):
    def test_generate_and_apply(self):
        out = StringIO()
        call_command("generate_timetable", "--time-limit", "10", stdout=out)
        self.assertIn("Applied: 22 lesson(s) assigned", out.getvalue())
        self.assertEqual(TimetableEntry.objects.count(), 22)

    def test_no_apply_stores_proposal_only(self):
        out = StringIO()
        call_command("generate_timetable", "--time-limit", "10", "--no-apply", stdout=out)
        self.assertEqual(TimetableEntry.objects.count(), 0)
        self.assertEqual(GenerationRun.objects.get().status, "succeeded")

    def test_refusal_is_an_error(self):
        TeacherDailyAvailability.objects.filter(teacher=self.ms_maths).update(max_periods=1)
        with self.assertRaises(CommandError):
            call_command("generate_timetable", "--time-limit", "5", stdout=StringIO())


class GeneratorPageTests(SmallSchool):
    def test_index_renders_for_each_role(self):
        for user in (self.editor, self.viewer):
            self.client.force_login(user)
            response = self.client.get(reverse("generator:index"))
            self.assertEqual(response.status_code, 200)
        self.assertContains(response, "read-only access")

    def test_run_review_and_apply_via_pages(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse("generator:index"),
            {"keep_mode": KEEP_ALL, "mode": "complete", "time_limit": 10, "random_seed": 3},
        )
        run = GenerationRun.objects.get()
        self.assertRedirects(response, reverse("generator:run_detail", args=[run.pk]))
        self.assertEqual(run.random_seed, 3)
        detail = reverse("generator:run_detail", args=[run.pk])
        for query in ("", f"?class={self.c10b.pk}", f"?view=teacher&teacher={self.mr_english.pk}", "?view=teacher&teacher=x"):
            with self.subTest(query=query):
                page = self.client.get(detail + query)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "Apply this timetable")
                self.assertContains(page, "Required lessons placed")
        self.assertContains(self.client.get(detail), "diff-new")
        response = self.client.post(reverse("generator:apply_run", args=[run.pk]), follow=True)
        self.assertRedirects(response, reverse("scheduling:dashboard"))
        self.assertContains(response, "22 lesson(s) assigned")
        self.assertEqual(TimetableEntry.objects.count(), 22)
        self.assertContains(self.client.get(detail), "Applied")

    def test_invalid_form_is_rejected(self):
        self.client.force_login(self.editor)
        response = self.client.post(reverse("generator:index"), {"keep_mode": "nope", "mode": "complete", "time_limit": 0, "random_seed": 0})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(GenerationRun.objects.exists())

    def test_viewer_cannot_run_or_apply(self):
        run = services.run_generation(time_limit=10)
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("generator:index"), {"keep_mode": KEEP_ALL, "mode": "complete", "time_limit": 5, "random_seed": 0})
        self.assertEqual(response.status_code, 403)
        page = self.client.get(reverse("generator:run_detail", args=[run.pk]))
        self.assertNotContains(page, "Apply this timetable")
        self.assertEqual(self.client.post(reverse("generator:apply_run", args=[run.pk])).status_code, 403)
        self.assertEqual(TimetableEntry.objects.count(), 0)

    def test_anonymous_is_redirected(self):
        for url in (reverse("generator:index"), reverse("generator:run_detail", args=[1])):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(reverse("generator:apply_run", args=[1])).status_code, 302)

    def test_refused_run_page_lists_reasons(self):
        TeacherDailyAvailability.objects.filter(teacher=self.ms_maths).update(max_periods=1)
        run = services.run_generation(time_limit=5)
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("generator:run_detail", args=[run.pk]))
        self.assertContains(page, "Refused")
        self.assertContains(page, "Ms Maths is designated")
        index = self.client.get(reverse("generator:index"))
        self.assertContains(index, "must be fixed first")


class ScoringTests(SmallSchool):
    def test_score_and_compare(self):
        from generator.scoring import compare, score

        run = services.run_generation(time_limit=10)
        proposal = score(run.lessons.all())
        self.assertEqual((proposal["placed"], proposal["unmet"]), (22, 0))
        self.assertEqual(proposal["subject_daily_excess"], 0)  # 4 lessons over 3 days fit 2-a-day
        rows = {row["key"]: row for row in compare(proposal, score([]))}
        self.assertEqual(rows["placed"]["verdict"], "better")
        self.assertEqual(rows["teacher_gaps"]["verdict"], "same")  # not comparable with an empty timetable
        services.apply_run(run, self.editor)
        rows = {row["key"]: row for row in compare(proposal, score(TimetableEntry.objects.all()))}
        self.assertTrue(all(row["verdict"] == "same" for row in rows.values()))
