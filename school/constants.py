"""Shared constants for the school domain."""

from django.db import models


class Day(models.IntegerChoices):
    """Days of the week, stored as integers (Monday = 0, matching Python's weekday())."""

    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


DAY_SHORT_LABELS = {
    Day.MONDAY: "MON",
    Day.TUESDAY: "TUE",
    Day.WEDNESDAY: "WED",
    Day.THURSDAY: "THU",
    Day.FRIDAY: "FRI",
    Day.SATURDAY: "SAT",
    Day.SUNDAY: "SUN",
}

# Hard upper bound used by field validators. The real per-day limit comes from
# SchoolSettings / SchoolDayConfiguration via school.day_schedule.
MAX_PERIODS_PER_DAY = 12

DEFAULT_PERIODS_PER_DAY = 9
DEFAULT_RECESS_AFTER_PERIOD = 4
# The default operating days (Monday–Saturday) are seeded by the data
# migration school/0002_seed_school_calendar.py.
