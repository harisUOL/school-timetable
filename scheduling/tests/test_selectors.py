from school.constants import Day
from school.models import SchoolDayConfiguration
from scheduling import selectors

from .fixtures import SchoolFixture


class ClassGridTests(SchoolFixture):
    def test_rows_columns_and_recess(self):
        self.assign(period=2)
        grid = selectors.class_grid(self.c10a)
        self.assertEqual([r.short_label for r in grid.rows], ["MON", "TUE", "WED", "THU", "FRI", "SAT"])
        labels = [c["label"] for c in grid.columns]
        self.assertEqual(labels, ["I", "II", "III", "IV", "Recess", "V", "VI", "VII", "VIII", "IX"])
        monday = grid.rows[0].cells
        self.assertEqual([c.kind for c in monday][:5], ["empty", "lesson", "empty", "empty", "recess"])
        self.assertEqual(monday[1].entry.teacher, self.ms_maths)
        self.assertEqual((grid.filled, grid.total_slots), (1, 51))

    def test_short_day_trailing_cells_are_not_slots(self):
        saturday = selectors.class_grid(self.c10a).rows[-1]
        kinds = [c.kind for c in saturday.cells]
        self.assertEqual(len(kinds), 10)
        self.assertEqual(kinds[-3:], ["none", "none", "none"])
        self.assertFalse(any(c.is_slot for c in saturday.cells[-3:]))

    def test_recess_follows_each_days_position(self):
        SchoolDayConfiguration.objects.filter(day=Day.TUESDAY).update(recess_after_period=3)
        SchoolDayConfiguration.objects.filter(day=Day.WEDNESDAY).update(recess_after_period=0)
        grid = selectors.class_grid(self.c10a)
        rows = grid.rows
        self.assertEqual([c["kind"] for c in grid.columns][3:6], ["recess", "period", "recess"])
        self.assertEqual([c.kind for c in rows[1].cells][3:6], ["recess", "empty", "gap"])  # Tuesday after III
        self.assertEqual([c.kind for c in rows[0].cells][3:6], ["gap", "empty", "recess"])  # Monday after IV
        self.assertNotIn("recess", [c.kind for c in rows[2].cells])
        self.assertEqual(len({len(r.cells) for r in rows}), 1)  # every row is the same width
        # Period N sits in the same column on every day.
        for index, column in enumerate(grid.columns):
            if column["kind"] == "period":
                self.assertEqual({r.cells[index].period for r in rows if r.cells[index].kind != "none"}, {column["period"]})

    def test_roman_numerals(self):
        self.assertEqual([selectors.roman(n) for n in (1, 4, 9, 12)], ["I", "IV", "IX", "XII"])


class ProgressAndLoadTests(SchoolFixture):
    def test_requirement_progress(self):
        self.assign(period=1)
        self.assign(period=2, subject=self.physics, teacher=self.mr_physics)
        progress = selectors.requirement_progress(self.c10a)
        rows = {(p.subject, p.required, p.assigned, p.remaining) for p in progress.subjects}
        self.assertEqual(rows, {("Mathematics", 6, 1, 5), ("Physics", 2, 1, 1)})
        self.assertEqual((progress.required, progress.assigned, progress.remaining), (8, 2, 6))
        self.assertFalse(progress.over_capacity)

    def test_teacher_day_load_counts_all_classes(self):
        self.assign(self.c10a, period=1, teacher=self.mr_physics)
        self.assign(self.c10b, period=2, teacher=self.mr_physics)
        loads = selectors.teacher_day_load([self.mr_physics.pk, self.ms_monday.pk, self.mr_bio.pk], Day.MONDAY)
        self.assertEqual(loads[self.mr_physics.pk].label, "2/5")
        self.assertEqual(loads[self.ms_monday.pk].max, 2)
        loads = selectors.teacher_day_load([self.ms_monday.pk], Day.TUESDAY)
        self.assertIsNone(loads[self.ms_monday.pk].max)


class SlotOptionsTests(SchoolFixture):
    def maths_teachers(self, options):
        subject = next(s for s in options["subjects"] if s["name"] == "Mathematics")
        return {t["name"]: t for t in subject["teachers"]}

    def test_lists_only_qualified_active_teachers_with_status(self):
        self.assign(self.c10b, period=1, teacher=self.mr_physics)
        options = selectors.slot_options(self.c10a, Day.MONDAY, 1)
        teachers = self.maths_teachers(options)
        self.assertEqual(set(teachers), {"Ms Maths", "Mr Physics", "Ms Monday"})
        self.assertNotIn("Mr Unqualified", teachers)
        self.assertEqual(teachers["Ms Maths"]["status"], "available")
        self.assertTrue(teachers["Ms Maths"]["designated"])
        clash = teachers["Mr Physics"]
        self.assertEqual(clash["status"], "blocked")
        self.assertEqual(clash["reasons"][0]["code"], "TEACHER_CLASH")
        self.assertIn("10B", clash["reasons"][0]["message"])
        self.assertEqual(clash["day_load"], {"assigned": 1, "max": 5})
        self.assertEqual(clash["week_load"], {"assigned": 1, "max": 30})
        # Designated teacher is listed first.
        subject = next(s for s in options["subjects"] if s["name"] == "Mathematics")
        self.assertEqual(subject["teachers"][0]["name"], "Ms Maths")

    def test_capacity_and_missing_capacity_statuses(self):
        self.assign(self.c10a, period=2, teacher=self.ms_monday)
        self.assign(self.c10b, period=3, teacher=self.ms_monday)
        teachers = self.maths_teachers(selectors.slot_options(self.c10a, Day.MONDAY, 1))
        self.assertEqual(teachers["Ms Monday"]["reasons"][0]["code"], "CAPACITY_EXCEEDED")
        teachers = self.maths_teachers(selectors.slot_options(self.c10a, Day.TUESDAY, 1))
        self.assertEqual(teachers["Ms Monday"]["reasons"][0]["code"], "NO_CAPACITY_CONFIGURED")

    def test_met_subjects_are_offered_as_extra_lessons(self):
        self.assign(period=1, subject=self.physics, teacher=self.mr_physics)
        self.assign(period=2, subject=self.physics, teacher=self.mr_physics)
        options = selectors.slot_options(self.c10a, Day.MONDAY, 3)
        physics = next(s for s in options["subjects"] if s["name"] == "Physics")
        self.assertTrue(physics["met"])
        self.assertEqual(physics["note"], "All 2 placed: another one is an extra lesson.")
        # Subjects the class does not take are offered too, for cover.
        biology = next(s for s in options["subjects"] if s["name"] == "Biology")
        self.assertEqual(biology["kind"], "other")
        self.assertEqual([t["name"] for t in biology["teachers"]], ["Mr Unqualified"])

    def test_occupied_slot_excludes_its_own_lesson(self):
        self.assign(period=1, subject=self.physics, teacher=self.mr_physics)
        entry = self.assign(period=2, subject=self.physics, teacher=self.mr_physics).entry
        options = selectors.slot_options(self.c10a, Day.MONDAY, 2)
        self.assertEqual(options["entry"]["id"], entry.pk)
        physics = next(s for s in options["subjects"] if s["name"] == "Physics")
        self.assertFalse(physics["met"])
        teacher = next(t for t in physics["teachers"] if t["name"] == "Mr Physics")
        self.assertEqual(teacher["status"], "available")
        self.assertEqual(teacher["day_load"]["assigned"], 1)
