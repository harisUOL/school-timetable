from django.urls import reverse

from school.constants import Day
from school.models import SubjectRequirement, TeacherDailyAvailability, TeacherSubject
from school.tests.factories import make_user
from scheduling import selectors
from scheduling.models import TimetableEntry

from .fixtures import SchoolFixture


class WorkloadTests(SchoolFixture):
    def setUp(self):
        # Mr Physics: Monday 10A P1 + 10B P2, Tuesday 10A P1. Ms Monday: Monday 10B P1.
        self.assign(self.c10a, Day.MONDAY, 1, teacher=self.mr_physics)
        self.assign(self.c10b, Day.MONDAY, 2, teacher=self.mr_physics)
        self.assign(self.c10a, Day.TUESDAY, 1, subject=self.physics, teacher=self.mr_physics)
        self.assign(self.c10b, Day.MONDAY, 1, teacher=self.ms_monday)

    def rows(self, **kwargs):
        week, rows = selectors.teacher_workloads(**kwargs)
        return week, {row.teacher.name: row for row in rows}

    def test_numbers_match_entries_exactly(self):
        week, rows = self.rows()
        for row in rows.values():
            for cell in row.cells:
                expected = TimetableEntry.objects.filter(teacher=row.teacher, day=cell.day).count()
                record = TeacherDailyAvailability.objects.filter(teacher=row.teacher, day=cell.day).first()
                with self.subTest(teacher=row.teacher.name, day=cell.day):
                    self.assertEqual(cell.assigned, expected)
                    self.assertEqual(cell.max, record.max_periods if record else None)
            self.assertEqual(row.assigned, TimetableEntry.objects.filter(teacher=row.teacher).count())
        physics = rows["Mr Physics"]
        self.assertEqual([c.label for c in physics.cells[:3]], ["2/5", "1/5", "0/5"])
        self.assertEqual((physics.assigned, physics.max, physics.remaining), (3, 30, 27))
        self.assertEqual(len(week), 6)

    def test_states(self):
        _, rows = self.rows()
        monday = rows["Ms Monday"].cells
        self.assertEqual(monday[0].state, "near")  # 1 of 2: one left
        self.assertEqual(monday[1].state, "none")  # no record on Tuesday
        self.assertEqual(rows["Mr Physics"].cells[2].state, "free")
        self.assign(self.c10a, Day.MONDAY, 3, teacher=self.ms_monday)
        _, rows = self.rows()
        self.assertEqual(rows["Ms Monday"].cells[0].state, "full")
        TeacherDailyAvailability.objects.filter(teacher=self.ms_monday).update(max_periods=1)
        _, rows = self.rows()
        self.assertEqual(rows["Ms Monday"].cells[0].state, "over")
        self.assertEqual(rows["Ms Monday"].state, "over")

    def test_filter_by_subject_and_sort_by_load(self):
        _, rows = self.rows(subject_id=self.physics.pk)
        self.assertEqual(set(rows), {"Mr Physics"})
        _, ordered = selectors.teacher_workloads(sort="load")
        self.assertEqual(ordered[0].teacher.name, "Ms Monday")  # 1/2 = 50%
        _, ordered = selectors.teacher_workloads(sort="remaining")
        self.assertEqual(ordered[0].teacher.name, "Mr Unqualified")  # 30 left, no lessons

    def test_teacher_grid_spans_all_classes(self):
        grid = selectors.teacher_grid(self.mr_physics)
        monday = grid.rows[0]
        lessons = [(c.period, str(c.entry.class_group)) for c in monday.cells if c.entry]
        self.assertEqual(lessons, [(1, "10A"), (2, "10B")])
        self.assertEqual(monday.load.label, "2/5")
        self.assertEqual(grid.filled, 3)


class ConflictTests(SchoolFixture):
    def codes(self):
        return {group.code: group for group in selectors.find_conflicts()}

    def test_no_conflicts_for_a_valid_timetable(self):
        self.assign()
        self.assertEqual(selectors.find_conflicts(), [])

    def test_capacity_lowered_below_assigned(self):
        self.assign(self.c10a, period=1, teacher=self.ms_monday)
        self.assign(self.c10b, period=2, teacher=self.ms_monday)
        TeacherDailyAvailability.objects.filter(teacher=self.ms_monday, day=Day.MONDAY).update(max_periods=1)
        group = self.codes()["CAPACITY_EXCEEDED"]
        conflict = group.conflicts[0]
        self.assertIn("Ms Monday has 2 lessons on Monday but may now teach only 1", conflict.message)
        self.assertEqual({(s.class_id, s.period) for s in conflict.slots}, {(self.c10a.pk, 1), (self.c10b.pk, 2)})

    def test_capacity_record_removed(self):
        self.assign(teacher=self.ms_monday)
        TeacherDailyAvailability.objects.filter(teacher=self.ms_monday).delete()
        self.assertIn("NO_CAPACITY_CONFIGURED", self.codes())

    def test_teacher_deactivated(self):
        self.assign()
        self.ms_maths.is_active = False
        self.ms_maths.save()
        conflict = self.codes()["TEACHER_INACTIVE"].conflicts[0]
        self.assertIn("Ms Maths is inactive", conflict.message)
        self.assertEqual(conflict.slots[0].label, "10A Mon I")

    def test_qualification_removed(self):
        self.assign(teacher=self.mr_physics)
        TeacherSubject.objects.filter(teacher=self.mr_physics, subject=self.maths).delete()
        self.assertIn("NOT_QUALIFIED", self.codes())

    def test_requirement_reduced_below_assigned(self):
        for period in (1, 2, 3):
            self.assign(period=period)
        SubjectRequirement.objects.filter(pk=self.req_10a_maths.pk).update(periods_per_week=1)
        conflict = self.codes()["REQUIREMENT_MET"].conflicts[0]
        self.assertIn("10A has 3 Mathematics lessons but the requirement is now 1. Remove 2.", conflict.message)
        self.assertEqual(len(conflict.slots), 3)

    def test_requirement_removed(self):
        self.assign(subject=self.physics, teacher=self.mr_physics)
        SubjectRequirement.objects.filter(pk=self.req_10a_physics.pk).delete()
        self.assertIn("SUBJECT_NOT_REQUIRED", self.codes())

    def test_conflict_count(self):
        self.assign()
        self.ms_maths.is_active = False
        self.ms_maths.save()
        self.assertEqual(selectors.conflict_count(), 1)


class ReportPageTests(SchoolFixture):
    def setUp(self):
        self.assign()
        self.assign(self.c10b, period=2, teacher=self.mr_physics)
        self.client.force_login(self.viewer)

    def get(self, name, *args, query=""):
        return self.client.get(reverse(f"scheduling:{name}", args=args) + query)

    def test_every_page_renders(self):
        pages = [
            ("dashboard", (), ""),
            ("workloads", (), ""),
            ("workloads", (), f"?subject={self.physics.pk}&sort=load"),
            ("workloads", (), "?sort=nonsense&subject=abc"),
            ("teachers", (), ""),
            ("teacher_timetable", (self.mr_physics.pk,), ""),
            ("subjects", (), ""),
            ("requirements", (), ""),
            ("requirements", (), f"?grade={self.c10a.grade_id}&class={self.c10a.pk}"),
            ("conflicts", (), ""),
        ]
        for name, args, query in pages:
            with self.subTest(page=name, query=query):
                self.assertEqual(self.get(name, *args, query=query).status_code, 200)

    def test_workloads_page(self):
        response = self.get("workloads")
        self.assertContains(response, "Mr Physics")
        self.assertContains(response, '<span class="load-value">1/5</span>', html=True)
        self.assertContains(response, "No capacity")

    def test_teacher_timetable_links_to_class_slots(self):
        response = self.get("teacher_timetable", self.mr_physics.pk)
        self.assertContains(response, reverse("scheduling:class_timetable", args=[self.c10b.pk]) + "#slot-0-2")
        self.assertEqual(self.get("teacher_timetable", 999).status_code, 404)

    def test_teacher_picker_redirects(self):
        response = self.get("teachers", query=f"?teacher={self.ms_maths.pk}")
        self.assertRedirects(response, reverse("scheduling:teacher_timetable", args=[self.ms_maths.pk]))

    def test_requirements_overview_flags_non_designated_teacher(self):
        self.assign(period=3, teacher=self.mr_physics)  # designated is Ms Maths
        response = self.get("requirements", query=f"?class={self.c10a.pk}")
        self.assertContains(response, "Differs")
        self.assertContains(response, "1 requirement is taught by someone other than the designated teacher")
        self.assertNotContains(response, reverse("scheduling:class_timetable", args=[self.c10b.pk]))

    def test_subjects_page_shows_demand_and_supply(self):
        response = self.get("subjects")
        self.assertContains(response, "Mathematics")
        self.assertContains(response, '<td class="num">12</td>', html=True)  # 6 + 6 maths demand

    def test_conflicts_page_links_to_slot(self):
        self.ms_maths.is_active = False
        self.ms_maths.save()
        response = self.get("conflicts")
        self.assertContains(response, "Lessons taught by an inactive teacher")
        self.assertContains(response, reverse("scheduling:class_timetable", args=[self.c10a.pk]) + "#slot-0-1")
        dashboard = self.get("dashboard")
        self.assertContains(dashboard, "Open the conflict report")
        self.assertContains(dashboard, "Most-loaded teachers")
        self.assertContains(dashboard, "Completion by class")

    def test_anonymous_and_roleless_access(self):
        self.client.logout()
        for name in ("workloads", "teachers", "subjects", "requirements", "conflicts"):
            with self.subTest(page=name):
                self.assertEqual(self.get(name).status_code, 302)
        self.client.force_login(make_user("nobody"))
        self.assertEqual(self.get("workloads").status_code, 403)
