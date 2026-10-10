import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase
from openpyxl import Workbook

from school.allotment import COMBINED, PARALLEL, parse_allotment, plan_import
from school.constants import Day
from school.day_schedule import get_day_schedule, weekly_teaching_slots
from school.health import run_health_checks
from school.models import (
    ClassGroup,
    SchoolDayConfiguration,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
)

ROWS = [
    ("Teacher", "Class", "Subject", "Periods/week"),
    (" A B Bhatt", None, None, "9"),
    (None, "VII A", "HIS", "3"),
    (None, "VII A", "CIV", "2"),
    (None, "VII A,VII B", "AF6-10", "2"),  # combined: skipped
    (None, None, None, None),
    ("Alina Abbas", None, None, "8"),
    (None, "VII B", "SCI", "7"),
    (None, "IX A", "ScL", "1"),  # parallel with Farheen: skipped
    ("Farheen Sabir", None, None, "4"),
    (None, "IX A", "ScL", "1"),
    (None, "Sr Nur A", "MUS", "1"),
    (None, "VII B", "AF6-10", "2"),
]


def workbook(rows=ROWS, sheet="Allotment"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    path = Path(tempfile.mkdtemp()) / "allotment.xlsx"
    wb.save(path)
    return path


def run(*args):
    out = StringIO()
    call_command("import_allotment", *args, stdout=out)
    return out.getvalue()


class ParseTests(TestCase):
    def test_parse_and_plan(self):
        plan = plan_import(parse_allotment(workbook()))
        self.assertEqual(plan.teachers, ["A B Bhatt", "Alina Abbas", "Farheen Sabir"])
        self.assertEqual(plan.classes, ["Sr Nur A", "VII A", "VII B", "IX A"])
        self.assertEqual(plan.subjects["HIS"], "History")
        self.assertEqual(plan.subjects["AF6-10"], "AF6-10")  # unknown codes keep the code as name
        self.assertEqual(
            plan.requirements,
            {
                ("VII A", "HIS"): (3, "A B Bhatt"),
                ("VII A", "CIV"): (2, "A B Bhatt"),
                ("VII B", "SCI"): (7, "Alina Abbas"),
                ("Sr Nur A", "MUS"): (1, "Farheen Sabir"),
            },
        )
        reasons = {(a.teacher, a.subject): r for a, r in plan.skipped}
        self.assertEqual(reasons[("A B Bhatt", "AF6-10")], COMBINED)
        self.assertEqual(reasons[("Farheen Sabir", "AF6-10")], COMBINED)  # same class/subject as a combined row
        self.assertEqual(reasons[("Alina Abbas", "ScL")], PARALLEL)
        self.assertEqual(reasons[("Farheen Sabir", "ScL")], PARALLEL)

    def test_bad_workbooks(self):
        with self.assertRaisesMessage(CommandError, "no “Allotment” sheet"):
            run(str(workbook(sheet="Other")))
        with self.assertRaisesMessage(CommandError, "is not a number"):
            run(str(workbook([ROWS[0], ("T", None, None, "1"), (None, "VII A", "HIS", "three")])))
        with self.assertRaisesMessage(CommandError, "expected a grade and a section"):
            run(str(workbook([ROWS[0], ("T", None, None, "1"), (None, "Seven", "HIS", "3")])))
        with self.assertRaisesMessage(CommandError, "Cannot open"):
            run("/nonexistent.xlsx")


class ImportCommandTests(TestCase):
    def test_imports_data_and_leaves_the_school_week_alone(self):
        SchoolDayConfiguration.objects.filter(day=Day.FRIDAY).update(periods=6)
        output = run(str(workbook()))
        self.assertIn("Imported: 4 classes", output)
        self.assertIn("Skipped", output)
        # The week is configured on the Settings page; the import never changes it.
        self.assertEqual(get_day_schedule(Day.FRIDAY).periods, 6)
        self.assertEqual(get_day_schedule(Day.SATURDAY).periods, 9)
        self.assertEqual(weekly_teaching_slots(), 51)
        self.assertEqual(sorted(str(c) for c in ClassGroup.objects.all()), ["IX A", "Sr Nur A", "VII A", "VII B"])
        bhatt = Teacher.objects.get(name="A B Bhatt")
        self.assertEqual(
            dict(bhatt.daily_availability.values_list("day", "max_periods")),
            {Day.MONDAY: 9, Day.TUESDAY: 9, Day.WEDNESDAY: 9, Day.THURSDAY: 9, Day.FRIDAY: 6, Day.SATURDAY: 9},
        )
        self.assertTrue(bhatt.is_qualified_for(Subject.objects.get(code="AF6-10")))
        req = SubjectRequirement.objects.get(class_group__section__name="A", class_group__grade__name="VII", subject__code="HIS")
        self.assertEqual((req.periods_per_week, req.teacher), (3, bhatt))
        self.assertEqual(SubjectRequirement.objects.count(), 4)
        self.assertEqual([i.message for i in run_health_checks() if i.is_error], [])

    def test_is_idempotent_keeps_capacities_and_syncs_requirements(self):
        run(str(workbook()))
        bhatt = Teacher.objects.get(name="A B Bhatt")
        TeacherDailyAvailability.objects.filter(teacher=bhatt, day=Day.MONDAY).update(max_periods=4)
        rows = [r for r in ROWS if r[2] != "CIV"]  # CIV dropped from the sheet
        run(str(workbook(rows)))
        self.assertEqual(Teacher.objects.count(), 3)
        self.assertEqual(bhatt.capacity_for_day(Day.MONDAY), 4)  # refined capacity kept
        self.assertFalse(SubjectRequirement.objects.filter(subject__code="CIV").exists())

    def test_dry_run_saves_nothing(self):
        output = run(str(workbook()), "--dry-run")
        self.assertIn("Would import: 4 classes", output)
        self.assertFalse(ClassGroup.objects.exists())

    def test_report_csv(self):
        report = Path(tempfile.mkdtemp()) / "skipped.csv"
        run(str(workbook()), "--report", str(report))
        lines = report.read_text().splitlines()
        self.assertEqual(lines[0], "Row,Teacher,Classes,Subject,Periods/week,Reason")
        self.assertEqual(len(lines), 5)
