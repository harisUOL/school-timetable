"""
Read-side helpers describing the school week.

Everything that needs to know "which days exist, how many periods each day
has, and where recess goes" calls these functions. Nothing else should
hardcode period numbers: the timetable grid, the scheduling validators and
the future generator all read from here.
"""

from dataclasses import dataclass

from .constants import DAY_SHORT_LABELS, Day


@dataclass(frozen=True)
class DaySchedule:
    day: int
    periods: int
    recess_after: int  # 0 means no recess that day

    @property
    def label(self) -> str:
        return Day(self.day).label

    @property
    def short_label(self) -> str:
        return DAY_SHORT_LABELS[self.day]

    @property
    def period_numbers(self) -> range:
        return range(1, self.periods + 1)

    @property
    def has_recess(self) -> bool:
        return 0 < self.recess_after < self.periods

    def is_valid_period(self, period) -> bool:
        return isinstance(period, int) and 1 <= period <= self.periods


def get_week_schedule() -> list[DaySchedule]:
    """All operating days in order, with their effective layout (2 queries)."""
    from .models import SchoolDayConfiguration, SchoolSettings

    settings = SchoolSettings.load()
    return [
        DaySchedule(
            day=config.day,
            periods=config.effective_periods(settings),
            recess_after=config.effective_recess_after(settings),
        )
        for config in SchoolDayConfiguration.objects.filter(is_operating=True).order_by("day")
    ]


def get_day_schedule(day) -> DaySchedule | None:
    """The layout for one day, or None if the school does not operate then."""
    for schedule in get_week_schedule():
        if schedule.day == day:
            return schedule
    return None


def is_valid_slot(day, period) -> bool:
    schedule = get_day_schedule(day)
    return schedule is not None and schedule.is_valid_period(period)


def periods_for_day(day) -> int:
    schedule = get_day_schedule(day)
    return schedule.periods if schedule else 0


def weekly_teaching_slots() -> int:
    """Teaching periods per class per week (recess excluded)."""
    return sum(schedule.periods for schedule in get_week_schedule())


def max_periods_in_any_day() -> int:
    return max((schedule.periods for schedule in get_week_schedule()), default=0)
