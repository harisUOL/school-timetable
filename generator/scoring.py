"""
Quality metrics for any timetable (a proposal or the live one), so the review
page can compare them like for like. Lower is better except where noted.
"""

from collections import defaultdict

from django.conf import settings

from school.day_schedule import Calendar
from school.models import SubjectRequirement, TeacherDailyAvailability, TeacherSubject

from .timeline import home_period, home_schedule

METRIC_LABELS = [
    ("placed", "Required lessons placed", "higher"),
    ("unmet", "Required lessons missing", "lower"),
    ("subject_daily_excess", "Lessons beyond the daily limit for a subject", "lower"),
    ("subject_spread", "Subject spread across the week (%)", "higher"),
    ("long_runs", "Teacher lessons beyond the consecutive limit", "lower"),
    ("teacher_gaps", "Free periods inside teachers' days", "lower"),
    ("balance_spread", "Average utilisation spread between teachers sharing a subject (%)", "lower"),
]


def score(lessons, *, calendar=None, requirements=None, capacities=None, qualified=None):
    """
    `lessons`: iterable of objects with class_id/class_group_id, day, period,
    subject_id and teacher_id attributes. Teachers' runs and gaps are counted
    on each teacher's home bell schedule for the day (generator/timeline.py).
    """
    calendar = calendar if calendar is not None else Calendar.load()
    days = {s.day: s for s in calendar.school_week}
    if requirements is None:
        requirements = {
            (c, s): n
            for c, s, n in SubjectRequirement.objects.values_list("class_group_id", "subject_id", "periods_per_week")
        }
    if capacities is None:
        capacities = {
            (t, d): m
            for t, d, m in TeacherDailyAvailability.objects.values_list("teacher_id", "day", "max_periods")
        }
    if qualified is None:
        qualified = set(TeacherSubject.objects.filter(teacher__is_active=True).values_list("teacher_id", "subject_id"))

    per_class_subject = defaultdict(int)
    per_class_subject_day = defaultdict(int)
    teacher_lessons = defaultdict(list)  # (teacher, day) -> [(class, period)]
    teacher_week = defaultdict(int)
    for lesson in lessons:
        class_id = getattr(lesson, "class_id", None) or lesson.class_group_id
        per_class_subject[(class_id, lesson.subject_id)] += 1
        per_class_subject_day[(class_id, lesson.subject_id, lesson.day)] += 1
        teacher_lessons[(lesson.teacher_id, lesson.day)].append((class_id, lesson.period))
        teacher_week[lesson.teacher_id] += 1

    required = sum(requirements.values())
    placed = sum(min(per_class_subject[key], n) for key, n in requirements.items())

    limit = settings.TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY
    excess = sum(max(n - limit, 0) for n in per_class_subject_day.values())

    spread_scores = []
    for (class_id, subject_id), n in requirements.items():
        best = min(n, len(days))
        if not best:
            continue
        used = sum(1 for d in days if per_class_subject_day.get((class_id, subject_id, d)))
        spread_scores.append(min(used / best, 1.0))
    spread = round(100 * sum(spread_scores) / len(spread_scores)) if spread_scores else 100

    run_limit = settings.GENERATOR["MAX_CONSECUTIVE"]
    long_runs = gaps = 0
    for (teacher_id, d), items in teacher_lessons.items():
        schedule = home_schedule(calendar, d, [c for c, _p in items]) if d in days else None
        if schedule is None:
            continue
        periods = set()
        for c, p in items:
            interval = calendar.interval(c, d, p)
            mapped = home_period(schedule, interval) if interval else None
            if mapped is not None:
                periods.add(mapped)
        if not periods:
            continue
        gaps += (max(periods) - min(periods) + 1) - len(periods)
        run = 0
        for p in schedule.period_numbers:
            if p in periods:
                run += 1
                if run > run_limit:
                    long_runs += 1
            else:
                run = 0
            if schedule.has_recess and p == schedule.recess_after:
                run = 0

    weekly_cap = defaultdict(int)
    for (t, d), m in capacities.items():
        if d in days:
            weekly_cap[t] += m
    subjects = defaultdict(list)
    for t, s in qualified:
        if weekly_cap[t]:
            subjects[s].append(100 * teacher_week[t] / weekly_cap[t])
    spreads = [max(u) - min(u) for u in subjects.values() if len(u) > 1]
    balance = round(sum(spreads) / len(spreads)) if spreads else 0

    return {
        "lessons": sum(per_class_subject.values()),
        "required": required,
        "placed": placed,
        "unmet": required - placed,
        "subject_daily_excess": excess,
        "subject_spread": spread,
        "long_runs": long_runs,
        "teacher_gaps": gaps,
        "balance_spread": balance,
    }


def comparable(proposal, current):
    """Soft-quality figures only mean something when both timetables are about as complete."""
    return current.get("placed", 0) >= 0.9 * proposal.get("placed", 0)


def compare(proposal, current):
    """Rows for the review page: label, proposal value, current value, better/worse/same."""
    rows = []
    soft_ok = comparable(proposal, current)
    for key, label, direction in METRIC_LABELS:
        a, b = proposal.get(key), current.get(key)
        if key not in ("placed", "unmet") and not soft_ok:
            verdict = "same"
        elif a is None or b is None or a == b:
            verdict = "same"
        elif (a > b) == (direction == "higher"):
            verdict = "better"
        else:
            verdict = "worse"
        rows.append({"key": key, "label": label, "proposal": a, "current": b, "verdict": verdict})
    return rows
