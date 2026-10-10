"""
CP-SAT model for whole-school timetable generation.

Variables
- x[r, t, d, p] = 1 when requirement r (a class + subject) is taught by
  candidate teacher t in slot (d, p). Variables only exist for slots that are
  inside day d's periods, where the class and teacher are not already busy
  with a kept lesson, and where t has capacity left on d.
- Candidates: the designated teacher if r has one (fixed); otherwise every
  qualified active teacher, with y[r, t] choosing exactly ONE of them for the
  whole week (x <= y).

Hard constraints
- Lessons per requirement: exactly the periods still needed (complete mode)
  or at most that many (best-effort mode, which maximises lessons placed).
- At most one lesson per class per slot.
- A teacher's lessons never overlap in time, across all classes. Timing groups
  ring different bells, so this is by clock time: for every moment a lesson
  could start, at most one of the teacher's possible lessons covers it.
- Lessons per teacher per day <= max_periods (kept lessons count too).
- Kept lessons are constants: they occupy their slots and capacity.

Soft constraints (weights in settings.GENERATOR["WEIGHTS"])
- At most N lessons of a subject per class per day (N = TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY).
- Spread each subject across the week (reward each distinct day used).
- Avoid more than MAX_CONSECUTIVE lessons in a row for a teacher (recess breaks a run).
- Minimise free periods between a teacher's first and last lesson of a day.
  Both are counted on the teacher's home bell schedule (generator/timeline.py).
- Balance utilisation among teachers who share a subject.

Configuration is read through `school.day_schedule` and the scheduling read
layer; nothing here writes to the database.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings
from ortools.sat.python import cp_model

from school.day_schedule import Calendar, overlaps
from school.models import ClassGroup, Subject, SubjectRequirement, Teacher, TeacherDailyAvailability
from scheduling.models import TimetableEntry
from scheduling.validators import Lesson, _load_qualifications

from .timeline import home_period, home_schedule

# --------------------------------------------------------------------------
# Problem data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    id: int
    class_id: int
    subject_id: int
    periods: int
    designated_id: int | None


@dataclass
class Problem:
    calendar: Calendar
    classes: dict  # id -> label
    subjects: dict  # id -> name
    teachers: dict  # active teachers: id -> name
    requirements: list
    qualified: set  # {(teacher_id, subject_id)}
    capacity: dict  # (teacher_id, day) -> max_periods (missing means 0)
    kept: list  # Lesson (key = entry id) fixed in place
    max_subject_per_day: int = 2

    @property
    def week(self):
        """The school's operating days (layouts differ per class: use class_week)."""
        return self.calendar.school_week

    def class_week(self, class_id):
        return self.calendar.class_week(class_id)

    def label(self, requirement):
        return f"{self.classes[requirement.class_id]} {self.subjects[requirement.subject_id]}"


def load_problem(*, keep_locked_only):
    entries = TimetableEntry.objects.with_related()
    if keep_locked_only:
        entries = entries.filter(is_locked=True)
    kept = [
        Lesson(
            key=e.pk,
            class_id=e.class_group_id,
            day=e.day,
            period=e.period,
            subject_id=e.subject_id,
            teacher_id=e.teacher_id,
            class_label=str(e.class_group),
            subject_name=e.subject.name,
            teacher_name=e.teacher.name,
        )
        for e in entries
    ]
    return Problem(
        calendar=Calendar.load(),
        classes={c.pk: str(c) for c in ClassGroup.objects.with_structure()},
        subjects=dict(Subject.objects.values_list("pk", "name")),
        teachers=dict(Teacher.objects.filter(is_active=True).values_list("pk", "name")),
        requirements=[
            Requirement(r.pk, r.class_group_id, r.subject_id, r.periods_per_week, r.teacher_id)
            for r in SubjectRequirement.objects.order_by("class_group_id", "subject_id")
        ],
        qualified=_load_qualifications(),
        capacity={
            (t, d): m
            for t, d, m in TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods")
        },
        kept=kept,
        max_subject_per_day=settings.TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY,
    )


# --------------------------------------------------------------------------
# Derived facts shared by the model and the diagnosis
# --------------------------------------------------------------------------


class Analysis:
    """What is left to schedule once kept lessons are in place."""

    def __init__(self, problem):
        self.problem = problem
        days = {s.day for s in problem.week}
        self.class_busy = {(k.class_id, k.day, k.period) for k in problem.kept}
        # Clock times the teacher is already busy with kept lessons: {(teacher, day): [(start, end)]}
        self.teacher_kept = defaultdict(list)
        for k in problem.kept:
            interval = problem.calendar.interval(k.class_id, k.day, k.period)
            if interval:
                self.teacher_kept[(k.teacher_id, k.day)].append(interval)
        kept_day = defaultdict(int)
        kept_req = defaultdict(int)
        self.kept_teachers = defaultdict(set)
        self.kept_class_subject_day = defaultdict(int)
        for k in problem.kept:
            kept_day[(k.teacher_id, k.day)] += 1
            kept_req[(k.class_id, k.subject_id)] += 1
            self.kept_teachers[(k.class_id, k.subject_id)].add(k.teacher_id)
            self.kept_class_subject_day[(k.class_id, k.subject_id, k.day)] += 1
        self.kept_day = kept_day
        self.cap_left = {
            (t, d): max(problem.capacity.get((t, d), 0) - kept_day[(t, d)], 0)
            for t in problem.teachers
            for d in days
        }
        self.need = {
            r.id: max(r.periods - kept_req[(r.class_id, r.subject_id)], 0) for r in problem.requirements
        }
        self.candidates = {r.id: self._candidates(r) for r in problem.requirements}

    def _candidates(self, r):
        p = self.problem
        if r.designated_id is not None:
            return [r.designated_id] if r.designated_id in p.teachers else []
        kept = sorted(t for t in self.kept_teachers[(r.class_id, r.subject_id)] if t in p.teachers)
        if kept:
            # Lessons already kept fix who teaches this class the subject.
            return kept
        return sorted(
            t
            for t in p.teachers
            if (t, r.subject_id) in p.qualified and any(self.cap_left[(t, s.day)] for s in p.week)
        )

    def teacher_free(self, teacher_id, day, interval):
        """True when no kept lesson of the teacher overlaps the interval."""
        return not any(overlaps(interval, busy) for busy in self.teacher_kept.get((teacher_id, day), ()))

    def weekly_cap_left(self, teacher_id):
        return sum(self.cap_left[(teacher_id, s.day)] for s in self.problem.week)


# --------------------------------------------------------------------------
# Solve
# --------------------------------------------------------------------------


@dataclass
class PlannedLesson:
    requirement_id: int
    class_id: int
    day: int
    period: int
    subject_id: int
    teacher_id: int


@dataclass
class Solution:
    status: str
    lessons: list = field(default_factory=list)
    objective: float | None = None
    duration: float = 0.0
    unmet: list = field(default_factory=list)  # [(requirement, missing)]

    @property
    def found(self):
        return self.status in ("OPTIMAL", "FEASIBLE")


def solve(problem, *, best_effort=False, time_limit=None, seed=0, workers=None, weights=None, soft=True):
    config = settings.GENERATOR
    weights = {**config["WEIGHTS"], **(weights or {})}
    analysis = Analysis(problem)
    model = cp_model.CpModel()
    builder = _Builder(problem, analysis, model, weights, best_effort, soft)
    builder.build()

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit or config["DEFAULT_TIME_LIMIT"])
    solver.parameters.num_search_workers = int(workers or config["WORKERS"])
    solver.parameters.random_seed = int(seed)
    started = time.monotonic()
    status = solver.Solve(model)
    duration = time.monotonic() - started

    solution = Solution(status=solver.StatusName(status), duration=duration)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solution
    solution.objective = solver.ObjectiveValue()
    for (rid, t, d, p), var in builder.x.items():
        if solver.Value(var):
            r = builder.requirements[rid]
            solution.lessons.append(PlannedLesson(rid, r.class_id, d, p, r.subject_id, t))
    placed = defaultdict(int)
    for lesson in solution.lessons:
        placed[lesson.requirement_id] += 1
    for r in problem.requirements:
        missing = analysis.need[r.id] - placed[r.id]
        if missing > 0:
            solution.unmet.append((r, missing))
    return solution


class _Builder:
    def __init__(self, problem, analysis, model, weights, best_effort, soft):
        self.p = problem
        self.a = analysis
        self.m = model
        self.w = weights
        self.best_effort = best_effort
        self.soft = soft
        self.requirements = {r.id: r for r in problem.requirements}
        self.x = {}
        self.penalties = []  # (weight, linear expression)
        self.kept_by_teacher_day = defaultdict(list)
        for k in problem.kept:
            self.kept_by_teacher_day[(k.teacher_id, k.day)].append(k)
        self.by_class_slot = defaultdict(list)
        self.by_teacher_time = defaultdict(list)  # (t, d) -> [((start, end), var, class_id)]
        self.by_teacher_day = defaultdict(list)
        self.by_req = defaultdict(list)
        self.by_req_day = defaultdict(list)
        self.by_req_teacher = defaultdict(list)
        self.by_teacher = defaultdict(list)

    def build(self):
        self._variables()
        self._hard()
        if self.soft:
            self._subject_per_day()
            self._consecutive_and_gaps()
            self._balance()
        objective = sum(weight * expr for weight, expr in self.penalties)
        self.m.Minimize(objective if self.penalties else 0)

    # -- variables -----------------------------------------------------------

    def _variables(self):
        for r in self.p.requirements:
            if not self.a.need[r.id]:
                continue
            for t in self.a.candidates[r.id]:
                for day in self.p.class_week(r.class_id):
                    if not self.a.cap_left[(t, day.day)]:
                        continue
                    for period in day.period_numbers:
                        d = day.day
                        interval = day.interval(period)
                        if (r.class_id, d, period) in self.a.class_busy or not self.a.teacher_free(t, d, interval):
                            continue
                        var = self.m.NewBoolVar(f"x_r{r.id}_t{t}_d{d}_p{period}")
                        key = (r.id, t, d, period)
                        self.x[key] = var
                        self.by_class_slot[(r.class_id, d, period)].append(var)
                        self.by_teacher_time[(t, d)].append((interval, var, r.class_id))
                        self.by_teacher_day[(t, d)].append(var)
                        self.by_req[r.id].append(var)
                        self.by_req_day[(r.id, d)].append(var)
                        self.by_req_teacher[(r.id, t)].append(var)
                        self.by_teacher[t].append(var)

    # -- hard rules ------------------------------------------------------------

    def _hard(self):
        for r in self.p.requirements:
            need = self.a.need[r.id]
            if not need:
                continue
            placed = sum(self.by_req[r.id])
            if self.best_effort:
                self.m.Add(placed <= need)
                self.penalties.append((self.w["unplaced_lesson"], need - placed))
            else:
                self.m.Add(placed == need)
            candidates = self.a.candidates[r.id]
            if len(candidates) > 1:
                chosen = {t: self.m.NewBoolVar(f"y_r{r.id}_t{t}") for t in candidates}
                self.m.AddExactlyOne(chosen.values())
                for t, y in chosen.items():
                    for var in self.by_req_teacher[(r.id, t)]:
                        self.m.AddImplication(var, y)
        for group in self.by_class_slot.values():
            if len(group) > 1:
                self.m.AddAtMostOne(group)
        for options in self.by_teacher_time.values():
            self._no_overlap(options)
        for (t, d), group in self.by_teacher_day.items():
            cap = self.a.cap_left[(t, d)]
            if len(group) > cap:
                self.m.Add(sum(group) <= cap)

    # -- soft rules ------------------------------------------------------------

    def _subject_per_day(self):
        limit = self.p.max_subject_per_day
        for (rid, d), group in self.by_req_day.items():
            r = self.requirements[rid]
            kept = self.a.kept_class_subject_day[(r.class_id, r.subject_id, d)]
            if len(group) + kept > limit:
                excess = self.m.NewIntVar(0, len(group) + kept, f"excess_r{rid}_d{d}")
                self.m.Add(excess >= sum(group) + kept - limit)
                self.penalties.append((self.w["subject_daily_excess"], excess))
            if kept:
                continue  # the subject is already on this day
            used = self.m.NewBoolVar(f"used_r{rid}_d{d}")
            self.m.AddMaxEquality(used, group)
            self.penalties.append((-self.w["subject_spread_day"], used))

    def _no_overlap(self, options):
        """
        Two intervals overlap exactly when one contains the other's start, so
        "at most one lesson covers each start time" forbids every overlap.
        """
        seen = set()
        for start in sorted({interval[0] for interval, _var, _c in options}):
            covering = [var for (s, e), var, _c in options if s <= start < e]
            key = frozenset(v.Index() for v in covering)
            if len(covering) > 1 and key not in seen:
                seen.add(key)
                self.m.AddAtMostOne(covering)

    def _home_busy(self, t, d):
        """The teacher's day on their home bell schedule: (home day, {period: bool | True | None})."""
        options = self.by_teacher_time.get((t, d), [])
        kept = self.kept_by_teacher_day.get((t, d), [])
        home = home_schedule(self.p.calendar, d, [c for _i, _v, c in options] + [k.class_id for k in kept])
        if home is None:
            return None, {}
        mapped = defaultdict(list)
        for interval, var, _c in options:
            period = home_period(home, interval)
            if period is not None:
                mapped[period].append(var)
        fixed = set()
        for k in kept:
            interval = self.p.calendar.interval(k.class_id, k.day, k.period)
            period = home_period(home, interval) if interval else None
            if period is not None:
                fixed.add(period)
        busy = {}
        for period in home.period_numbers:
            if period in fixed:
                busy[period] = True
            elif not mapped.get(period):
                busy[period] = None
            elif len(mapped[period]) == 1:
                busy[period] = mapped[period][0]
            else:
                b = self.m.NewBoolVar(f"busy_t{t}_d{d}_p{period}")
                self.m.AddMaxEquality(b, mapped[period])
                busy[period] = b
        return home, busy

    def _consecutive_and_gaps(self):
        run_limit = settings.GENERATOR["MAX_CONSECUTIVE"]
        for (t, d) in list(self.by_teacher_day):
            if not self.by_teacher_day.get((t, d)):
                continue
            day, busy = self._home_busy(t, d)
            if day is None:
                continue
            # Consecutive runs: a recess splits the day into two segments.
            segments = (
                [list(range(1, day.recess_after + 1)), list(range(day.recess_after + 1, day.periods + 1))]
                if day.has_recess
                else [list(day.period_numbers)]
            )
            for segment in segments:
                for i in range(len(segment) - run_limit):
                    window = [busy[p] for p in segment[i : i + run_limit + 1]]
                    if any(b is None for b in window):
                        continue
                    variables = [b for b in window if b is not True]
                    constant = len(window) - len(variables)
                    over = self.m.NewBoolVar(f"run_t{t}_d{d}_p{segment[i]}")
                    self.m.Add(sum(variables) + constant <= run_limit + over)
                    self.penalties.append((self.w["teacher_consecutive"], over))
            self._gaps(t, d, day, busy)

    def _gaps(self, t, d, day, busy):
        periods = list(day.period_numbers)
        bools = {}
        for p in periods:
            b = busy[p]
            if b is None:
                bools[p] = 0
            elif b is True:
                bools[p] = 1
            else:
                bools[p] = b
        before, after = {}, {}
        running = 0
        for p in periods:
            before[p] = running
            running = self._or(running, bools[p], f"before_t{t}_d{d}_p{p}")
        running = 0
        for p in reversed(periods):
            after[p] = running
            running = self._or(running, bools[p], f"after_t{t}_d{d}_p{p}")
        for p in periods:
            if (isinstance(before[p], int) and before[p] == 0) or (isinstance(after[p], int) and after[p] == 0):
                continue
            if isinstance(bools[p], int) and bools[p] == 1:
                continue
            gap = self.m.NewBoolVar(f"gap_t{t}_d{d}_p{p}")
            self.m.Add(gap >= before[p] + after[p] - 1 - bools[p])
            self.penalties.append((self.w["teacher_gap"], gap))

    def _or(self, a, b, name):
        if isinstance(a, int) and isinstance(b, int):
            return 1 if (a or b) else 0
        if isinstance(a, int):
            return 1 if a else b
        if isinstance(b, int):
            return 1 if b else a
        result = self.m.NewBoolVar(name)
        self.m.AddMaxEquality(result, [a, b])
        return result

    def _balance(self):
        groups = defaultdict(set)
        for r in self.p.requirements:
            if self.a.need[r.id] and len(self.a.candidates[r.id]) > 1:
                groups[r.subject_id].update(self.a.candidates[r.id])
        for subject_id, teachers in groups.items():
            if len(teachers) < 2:
                continue
            utilisation = []
            for t in teachers:
                weekly = sum(self.p.capacity.get((t, s.day), 0) for s in self.p.week)
                if not weekly:
                    continue
                k = round(1000 / weekly)
                kept = sum(self.a.kept_day[(t, s.day)] for s in self.p.week)
                utilisation.append(k * sum(self.by_teacher[t]) + k * kept)
            if len(utilisation) < 2:
                continue
            high = self.m.NewIntVar(0, 100_000, f"util_max_s{subject_id}")
            low = self.m.NewIntVar(0, 100_000, f"util_min_s{subject_id}")
            for u in utilisation:
                self.m.Add(high >= u)
                self.m.Add(low <= u)
            self.penalties.append((self.w["teacher_balance"], high - low))
