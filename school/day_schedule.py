"""
Read-side helpers describing the school week.

Everything that needs to know "which days exist, how many periods each day
has, where recess goes and when each period runs" reads it from here. Nothing
else should hardcode period numbers or times.

There are two levels:

- the **school week** (`SchoolSettings` + `SchoolDayConfiguration`): which days
  the school is open, and the default layout and bell times;
- **timing groups** (`TimingGroup` + `TimingGroupDay`): grades that keep their
  own layout and bell times on some days, e.g. Prep opening at 09:00 while
  Class VI–X opens at 07:30. A blank field inherits the school's value.

`Calendar` resolves both into one `DaySchedule` per class and day. Because the
groups' bells differ, the same period number can be at different times in
different classes: teacher clashes are decided by clock time
(`DaySchedule.interval`), never by period number.
"""

import datetime
from dataclasses import dataclass

from .constants import DAY_SHORT_LABELS, DEFAULT_PERIOD_MINUTES, DEFAULT_RECESS_MINUTES, Day

SCHOOL_WEEK = None  # the key of the school week among the calendar's schedules


def time_range_label(start, end):
    return f"{start:%H:%M}–{end:%H:%M}"


def _minutes(time):
    return time.hour * 60 + time.minute


def _clock(minutes):
    minutes = min(minutes, 24 * 60 - 1)
    return datetime.time(minutes // 60, minutes % 60)


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
        return _minutes(self.end) - _minutes(self.start)


@dataclass(frozen=True)
class DaySchedule:
    day: int
    periods: int
    recess_after: int  # 0 means no recess that day
    start_time: datetime.time = datetime.time(8, 0)
    period_minutes: int = DEFAULT_PERIOD_MINUTES
    recess_minutes: int = DEFAULT_RECESS_MINUTES
    group: int | None = SCHOOL_WEEK  # the timing group this layout belongs to

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

    def interval(self, period) -> tuple[int, int]:
        """(start, end) of a period in minutes after midnight."""
        start = _minutes(self.start_time) + (period - 1) * self.period_minutes
        if self.has_recess and period > self.recess_after:
            start += self.recess_minutes
        return start, start + self.period_minutes

    @property
    def end_minutes(self) -> int:
        return self.interval(self.periods)[1] if self.periods else _minutes(self.start_time)

    @property
    def timings(self) -> list[SlotTime]:
        """Every period of the day in order, with the recess (if any) in its place."""
        result = []
        for period in self.period_numbers:
            start, end = self.interval(period)
            result.append(SlotTime("period", period, _clock(start), _clock(end)))
            if self.has_recess and period == self.recess_after and self.recess_minutes:
                result.append(SlotTime("recess", period, _clock(end), _clock(end + self.recess_minutes)))
        return result

    def period_time(self, period) -> SlotTime | None:
        if not self.is_valid_period(period):
            return None
        start, end = self.interval(period)
        return SlotTime("period", period, _clock(start), _clock(end))

    def period_label(self, period) -> str:
        slot = self.period_time(period)
        return slot.label if slot else ""

    @property
    def recess_time(self) -> SlotTime | None:
        return next((slot for slot in self.timings if slot.kind == "recess"), None)

    @property
    def end_time(self) -> datetime.time:
        return _clock(self.end_minutes)

    @property
    def hours_label(self) -> str:
        return time_range_label(self.start_time, self.end_time)

    def same_layout(self, other) -> bool:
        return other is not None and (
            self.periods,
            self.recess_after,
            self.start_time,
            self.period_minutes,
            self.recess_minutes,
        ) == (other.periods, other.recess_after, other.start_time, other.period_minutes, other.recess_minutes)


def overlaps(a, b) -> bool:
    """Two (start, end) intervals share some time."""
    return a[0] < b[1] and b[0] < a[1]


# --------------------------------------------------------------------------
# The calendar: school week + timing groups, resolved per class
# --------------------------------------------------------------------------


def _pick(value, default):
    return default if value is None else value


class Calendar:
    """
    Every schedule in the school, loaded once (`Calendar.load()`, 5 queries).
    `with_changes()` returns a copy with unsaved edits applied, so a form can
    validate the calendar it is about to save.
    """

    def __init__(self, settings, configs, groups, group_days, grade_group, class_grade):
        self.settings = settings
        self.configs = configs  # day -> SchoolDayConfiguration
        self.groups = groups  # group id -> name, in display order
        self.group_days = group_days  # group id -> {day: TimingGroupDay}
        self.grade_group = grade_group  # grade id -> group id or None
        self.class_grade = class_grade  # class id -> grade id
        self.weeks = {SCHOOL_WEEK: self._school_week()}
        for group_id in groups:
            self.weeks[group_id] = self._group_week(group_id)
        self._by_day = {key: {s.day: s for s in week} for key, week in self.weeks.items()}

    @classmethod
    def load(cls):
        from .models import ClassGroup, Grade, SchoolDayConfiguration, SchoolSettings, TimingGroup, TimingGroupDay

        group_days = {}
        for row in TimingGroupDay.objects.all():
            group_days.setdefault(row.group_id, {})[row.day] = row
        return cls(
            SchoolSettings.load(),
            {c.day: c for c in SchoolDayConfiguration.objects.all()},
            dict(TimingGroup.objects.values_list("pk", "name")),
            group_days,
            dict(Grade.objects.values_list("pk", "timing_group_id")),
            dict(ClassGroup.objects.values_list("pk", "grade_id")),
        )

    def with_changes(self, *, settings=None, configs=None, groups=None, group_days=None, grade_group=None, removed_groups=()):
        """A copy with edits applied. `group_days` replaces each named group's days: {group: {day: row}}."""
        new_groups = {k: v for k, v in {**self.groups, **(groups or {})}.items() if k not in removed_groups}
        new_days = {k: dict(v) for k, v in self.group_days.items() if k not in removed_groups}
        for group_id, days in (group_days or {}).items():
            new_days[group_id] = dict(days)
        new_grade_group = {
            grade: (None if group in removed_groups else group)
            for grade, group in {**self.grade_group, **(grade_group or {})}.items()
        }
        return Calendar(
            settings or self.settings,
            {**self.configs, **(configs or {})},
            new_groups,
            new_days,
            new_grade_group,
            self.class_grade,
        )

    # -- building ----------------------------------------------------------

    def _school_week(self):
        s = self.settings
        week = []
        for day in sorted(self.configs):
            config = self.configs[day]
            if not config.is_operating:
                continue
            week.append(
                DaySchedule(
                    day=day,
                    periods=config.periods or s.default_periods_per_day,
                    recess_after=_pick(config.recess_after_period, s.default_recess_after_period),
                    start_time=config.start_time or s.day_start_time,
                    period_minutes=config.period_minutes or s.period_minutes,
                    recess_minutes=_pick(config.recess_minutes, s.recess_minutes),
                )
            )
        return week

    def _group_week(self, group_id):
        days = self.group_days.get(group_id, {})
        week = []
        for base in self.weeks[SCHOOL_WEEK]:
            row = days.get(base.day)
            if row is None:
                week.append(DaySchedule(**{**base.__dict__, "group": group_id}))
                continue
            week.append(
                DaySchedule(
                    day=base.day,
                    periods=row.periods or base.periods,
                    recess_after=_pick(row.recess_after_period, base.recess_after),
                    start_time=row.start_time or base.start_time,
                    period_minutes=row.period_minutes or base.period_minutes,
                    recess_minutes=_pick(row.recess_minutes, base.recess_minutes),
                    group=group_id,
                )
            )
        return week

    # -- lookups -----------------------------------------------------------

    @property
    def school_week(self):
        return self.weeks[SCHOOL_WEEK]

    @property
    def days(self):
        """The operating days, in order."""
        return [s.day for s in self.school_week]

    def group_name(self, group_id):
        return "School week" if group_id is SCHOOL_WEEK else self.groups.get(group_id, "")

    def group_of_class(self, class_id):
        if class_id not in self.class_grade:
            from .models import ClassGroup

            self.class_grade[class_id] = (
                ClassGroup.objects.filter(pk=class_id).values_list("grade_id", flat=True).first()
            )
        group = self.grade_group.get(self.class_grade[class_id])
        return group if group in self.weeks else SCHOOL_WEEK

    def week(self, group_id=SCHOOL_WEEK):
        return self.weeks.get(group_id, self.school_week)

    def class_week(self, class_id):
        return self.weeks[self.group_of_class(class_id)]

    def class_day(self, class_id, day):
        return self._by_day[self.group_of_class(class_id)].get(day)

    def class_weekly_slots(self, class_id):
        return sum(s.periods for s in self.class_week(class_id))

    def interval(self, class_id, day, period):
        """(start, end) minutes of a class's period, or None if it is not a teaching slot."""
        schedule = self.class_day(class_id, day)
        if schedule is None or not schedule.is_valid_period(period):
            return None
        return schedule.interval(period)

    def max_periods(self, day):
        """The longest day any class has on `day` (0 when the school is closed)."""
        return max((by_day[day].periods for by_day in self._by_day.values() if day in by_day), default=0)

    def schedules(self):
        """[(group id, name, week)] — the school week first, then each group."""
        return [(key, self.group_name(key), week) for key, week in self.weeks.items()]


def get_calendar() -> Calendar:
    return Calendar.load()


# --------------------------------------------------------------------------
# Validation shared by the models, the admin and the Settings page
# --------------------------------------------------------------------------


def calendar_errors(new, old=None, *, check_capacities=True):
    """
    Every reason why calendar `new` may not be saved, as messages. Checks the
    layouts themselves, then that existing lessons still fit their class's day
    and (unless `check_capacities` is False) that no daily capacity exceeds the
    longest day.
    """
    from scheduling.models import Activity, TimetableEntry

    from .models import ClassGroup, TeacherDailyAvailability

    errors = []
    if not new.days:
        errors.append("At least one day must be open.")

    for key, name, week in new.schedules():
        overridden = new.group_days.get(key, {}) if key is not SCHOOL_WEEK else None
        for s in week:
            if overridden is not None and s.day not in overridden:
                continue  # inherits the school's day, which is checked once
            prefix = s.label if key is SCHOOL_WEEK else f"{name}, {s.label}"
            if s.recess_after and s.recess_after >= s.periods:
                errors.append(
                    f"{prefix}: recess after period {s.recess_after} is not possible "
                    f"with only {s.periods} teaching periods."
                )
            if s.end_minutes > 24 * 60:
                errors.append(
                    f"{prefix}: {s.periods} periods of {s.period_minutes} minutes from "
                    f"{s.start_time:%H:%M} run past midnight."
                )

    stranded = {}
    for class_id, day, period in TimetableEntry.objects.values_list("class_group_id", "day", "period"):
        schedule = new.class_day(class_id, day)
        last = schedule.periods if schedule else 0
        if period > last:
            count, _ = stranded.get((class_id, day), (0, last))
            stranded[(class_id, day)] = (count + 1, last)
    for class_id, day, period in Activity.objects.values_list("class_group_id", "day", "period"):
        schedule = new.class_day(class_id, day)
        last = schedule.periods if schedule else 0
        if period > last:
            count, _ = stranded.get((class_id, day), (0, last))
            stranded[(class_id, day)] = (count + 1, last)
    if stranded:
        labels = {c.pk: str(c) for c in ClassGroup.objects.with_structure().filter(pk__in={c for c, _ in stranded})}
        for (class_id, day), (count, last) in sorted(stranded.items(), key=lambda i: (i[0][1], labels[i[0][0]])):
            errors.append(
                f"{Day(day).label}: {count} timetable lesson(s) or activity period(s) are scheduled beyond "
                f"period {last} for {labels[class_id]}. Clear them before reducing this day's periods."
            )

    if check_capacities:
        for day in Day.values:
            longest = new.max_periods(day)
            over = TeacherDailyAvailability.objects.filter(day=day, max_periods__gt=longest).count()
            if over:
                errors.append(
                    f"{Day(day).label}: {over} teacher availability record(s) allow more than "
                    f"{longest} periods. Lower them before reducing this day's periods."
                )
    return errors


def fit_capacities(old, new):
    """
    Bring daily capacities into line with the longest day of `new`. A full-day
    capacity (equal to the old longest day) follows the new one; anything else
    above the new length is lowered to it. Returns the number of records changed.
    """
    from .models import TeacherDailyAvailability

    changed = 0
    for day in Day.values:
        before, after = old.max_periods(day), new.max_periods(day)
        if before == after:
            continue
        records = TeacherDailyAvailability.objects.filter(day=day)
        if before and after:
            changed += records.filter(max_periods=before).update(max_periods=after)
        changed += records.filter(max_periods__gt=after).update(max_periods=after)
    return changed


# --------------------------------------------------------------------------
# Shortcuts
# --------------------------------------------------------------------------


def get_week_schedule() -> list[DaySchedule]:
    """The school week: every operating day in order with its default layout."""
    return Calendar.load().school_week


def get_class_week(class_group) -> list[DaySchedule]:
    """The week as a class lives it (its timing group's layout, or the school's)."""
    return Calendar.load().class_week(getattr(class_group, "pk", class_group))


def get_day_schedule(day, class_group=None) -> DaySchedule | None:
    """The layout for one day (for a class, if given), or None if the school is closed."""
    calendar = Calendar.load()
    if class_group is not None:
        return calendar.class_day(getattr(class_group, "pk", class_group), day)
    return next((s for s in calendar.school_week if s.day == day), None)


def is_valid_slot(day, period, class_group=None) -> bool:
    schedule = get_day_schedule(day, class_group)
    return schedule is not None and schedule.is_valid_period(period)


def periods_for_day(day, class_group=None) -> int:
    schedule = get_day_schedule(day, class_group)
    return schedule.periods if schedule else 0


def weekly_teaching_slots(class_group=None) -> int:
    """Teaching periods per week (recess excluded): the school week's, or a class's."""
    week = get_class_week(class_group) if class_group is not None else get_week_schedule()
    return sum(schedule.periods for schedule in week)


def max_periods_in_any_day() -> int:
    calendar = Calendar.load()
    return max((calendar.max_periods(d) for d in calendar.days), default=0)
