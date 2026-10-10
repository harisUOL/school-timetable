"""
Seeing a teacher's day on one bell schedule.

A teacher may teach classes in several timing groups whose bells differ, so
"periods in a row" and "free periods between lessons" need one grid to count
on. The teacher's *home* schedule for a day is the one most of their lessons
(or possible lessons) follow; each lesson is placed on the home period that
contains its midpoint. Lessons that fall outside the home day (e.g. in its
recess) are left out of these soft measures only; hard rules never use this.
"""

from collections import Counter


def home_schedule(calendar, day, class_ids):
    """The day schedule most of `class_ids` follow on `day` (ties: the school week first)."""
    counts = Counter(calendar.group_of_class(c) for c in class_ids)
    if not counts:
        return None
    order = list(calendar.weeks)
    group = max(counts, key=lambda g: (counts[g], -order.index(g)))
    return next((s for s in calendar.week(group) if s.day == day), None)


def home_period(home, interval):
    """The home period containing the interval's midpoint, or None."""
    middle = (interval[0] + interval[1]) / 2
    for period in home.period_numbers:
        start, end = home.interval(period)
        if start <= middle < end:
            return period
    return None
