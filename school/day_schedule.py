"""
Read-side helpers describing the school week.

Everything that needs to know "which days exist, how many periods each day
has, and where recess goes" calls these functions. Nothing else should
hardcode period numbers: the timetable grid, the scheduling validators and
the future generator all read from here.
"""

import datetime
from dataclasses import dataclass

from .constants import DAY_SHORT_LABELS, DEFAULT_PERIOD_MINUTES, DEFAULT_RECESS_MINUTES, Day


def time_range_label(start, end):
    return f"{start:%H:%M}–{end:%H:%M}"


@dataclass(frozen=True)
class SlotTime:
    """Bell times for one period (or the recess) of a day."""

    kind: str  # "period" | "recess"
    number: int  # the period number; for recess, the period it follows
    start: datetime.time
    end: datetime.time

    @property
    def label(self):
        return time_range_label(self.start, self.end)

    @property
    def minutes(self):
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)


@dataclass(frozen=True)
class DaySchedule:
    day: int
    periods: int
    recess_after: int  # 0 means no recess that day
    start_time: datetime.time = datetime.time(8, 0)
    period_minutes: int = DEFAULT_PERIOD_MINUTES
    recess_minutes: int = DEFAULT_RECESS_MINUTES

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

    @property
    def timings(self) -> list[SlotTime]:
        """Every period of the day in order, with the recess (if any) in its place."""
        day = datetime.date(2000, 1, 1)  # any date: only the clock time matters
        clock = datetime.datetime.combine(day, self.start_time)
        result = []
        for period in self.period_numbers:
            end = clock + datetime.timedelta(minutes=self.period_minutes)
            result.append(SlotTime("period", period, clock.time(), end.time()))
            clock = end
            if self.has_recess and period == self.recess_after and self.recess_minutes:
                end = clock + datetime.timedelta(minutes=self.recess_minutes)
                result.append(SlotTime("recess", period, clock.time(), end.time()))
                clock = end
        return result

    def period_time(self, period) -> SlotTime | None:
        for slot in self.timings:
            if slot.kind == "period" and slot.number == period:
                return slot
        return None

    @property
    def recess_time(self) -> SlotTime | None:
        return next((slot for slot in self.timings if slot.kind == "recess"), None)

    @property
    def end_time(self) -> datetime.time:
        timings = self.timings
        return timings[-1].end if timings else self.start_time

    @property
    def hours_label(self) -> str:
        return time_range_label(self.start_time, self.end_time)


def get_week_schedule() -> list[DaySchedule]:
    """All operating days in order, with their effective layout (2 queries)."""
    from .models import SchoolDayConfiguration, SchoolSettings

    settings = SchoolSettings.load()
    return [
        DaySchedule(
            day=config.day,
            periods=config.effective_periods(settings),
            recess_after=config.effective_recess_after(settings),
            start_time=config.effective_start_time(settings),
            period_minutes=config.effective_period_minutes(settings),
            recess_minutes=config.effective_recess_minutes(settings),
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
