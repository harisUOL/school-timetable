from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from scheduling.models import TimetableEntry
from scheduling.selectors import find_conflicts, school_completion

from .fixtures import FAST


@FAST
class DemoSchoolTests(TestCase):
    def test_seed_demo_then_generate_timetable_is_complete_and_valid(self):
        call_command("seed_demo", stdout=StringIO())
        out = StringIO()
        call_command("generate_timetable", "--time-limit", "10", stdout=out)
        self.assertIn("Every requirement is met", out.getvalue())
        completion = school_completion()
        self.assertEqual(completion["assigned"], completion["required"])
        self.assertEqual(TimetableEntry.objects.count(), completion["required"])
        # Re-check every lesson with the builder's validators: nothing breaks a rule.
        self.assertEqual(find_conflicts(), [])
