"""
Explain why a timetable cannot be completed.

`structural_bottlenecks()` runs quick counting arguments. Each one is a
necessary condition, so any message it returns is a definite reason, not a
guess. They run before solving (complete mode) and after an infeasible solve.

`best_effort_shortfall()` is the fallback when every count looks fine but the
pieces still cannot be packed together: it asks the solver for the most
lessons it can place and names the requirements left short.
"""

from collections import defaultdict

from school.constants import Day

from .model import Analysis


def _days(day_ids):
    return ", ".join(Day(d).label for d in sorted(day_ids)) or "no days"


def structural_bottlenecks(problem, analysis=None):
    a = analysis or Analysis(problem)
    p = problem
    reasons = []
    week = {s.day: s for s in p.week}
    free_slots = {
        c: {(s.day, n) for s in p.week for n in s.period_numbers if (c, s.day, n) not in a.class_busy}
        for c in p.classes
    }

    # 1. Requirements with nobody who can teach them.
    for r in p.requirements:
        if a.need[r.id] and not a.candidates[r.id]:
            if r.designated_id is not None:
                why = "its designated teacher is inactive"
            else:
                why = "no active qualified teacher has any capacity left"
            reasons.append(f"{p.label(r)}: {a.need[r.id]} lesson(s) needed but {why}.")

    # 2. Class: more lessons needed than free slots.
    need_by_class = defaultdict(int)
    for r in p.requirements:
        need_by_class[r.class_id] += a.need[r.id]
    for c, need in need_by_class.items():
        if need > len(free_slots[c]):
            reasons.append(
                f"{p.classes[c]} needs {need} more lessons but has only {len(free_slots[c])} free slots."
            )

    # 3. Class: on each day, lessons are limited by free slots and by what its teachers can give.
    for c, need in need_by_class.items():
        if not need:
            continue
        teachers = {t for r in p.requirements if r.class_id == c and a.need[r.id] for t in a.candidates[r.id]}
        usable = 0
        usable_days = set()
        for d, schedule in week.items():
            slots = sum(1 for (dd, _n) in free_slots[c] if dd == d)
            supply = sum(a.cap_left[(t, d)] for t in teachers)
            usable += min(slots, supply)
            if min(slots, supply):
                usable_days.add(d)
        if need > usable:
            reasons.append(
                f"{p.classes[c]} needs {need} more lessons, but its teachers can cover only {usable} "
                f"of its free slots (available on: {_days(usable_days)})."
            )

    # 4. Designated teacher: weekly load against capacity left.
    designated = defaultdict(list)
    for r in p.requirements:
        if r.designated_id is not None and a.need[r.id] and r.designated_id in p.teachers:
            designated[r.designated_id].append(r)
    for t, reqs in designated.items():
        need = sum(a.need[r.id] for r in reqs)
        cap = a.weekly_cap_left(t)
        if need > cap:
            classes = ", ".join(sorted({p.classes[r.class_id] for r in reqs}))
            reasons.append(
                f"{p.teachers[t]} is designated for {need} more lessons ({classes}) but has only "
                f"{cap} lessons of capacity left this week."
            )

    # 5. Designated teacher and class together: shared free slots, limited by daily capacity.
    by_pair = defaultdict(int)
    for t, reqs in designated.items():
        for r in reqs:
            by_pair[(t, r.class_id)] += a.need[r.id]
    for (t, c), need in by_pair.items():
        fit = 0
        for d in week:
            common = sum(
                1 for (dd, n) in free_slots[c] if dd == d and (t, d, n) not in a.teacher_busy
            )
            fit += min(common, a.cap_left[(t, d)])
        if need > fit:
            reasons.append(
                f"{p.teachers[t]} must teach {p.classes[c]} {need} more lessons, but they share only "
                f"{fit} free slots within {p.teachers[t]}'s daily capacity."
            )

    # 6. Subject: demand against the capacity of everyone who could teach it.
    need_by_subject = defaultdict(int)
    teachers_by_subject = defaultdict(set)
    for r in p.requirements:
        if a.need[r.id]:
            need_by_subject[r.subject_id] += a.need[r.id]
            teachers_by_subject[r.subject_id].update(a.candidates[r.id])
    for s, need in need_by_subject.items():
        supply = sum(a.weekly_cap_left(t) for t in teachers_by_subject[s])
        if need > supply:
            reasons.append(
                f"{p.subjects[s]} needs {need} more lessons but the teachers who can take them have "
                f"only {supply} lessons of capacity left in total."
            )
    return reasons


def best_effort_shortfall(problem, *, time_limit=20, seed=0):
    from .model import solve

    solution = solve(problem, best_effort=True, time_limit=time_limit, seed=seed, soft=False)
    if not solution.found:
        return [f"The solver could not find even a partial timetable ({solution.status})."]
    if not solution.unmet:
        return []
    total = sum(missing for _r, missing in solution.unmet)
    short = "; ".join(f"{problem.label(r)} ({missing})" for r, missing in solution.unmet[:12])
    more = f" and {len(solution.unmet) - 12} more" if len(solution.unmet) > 12 else ""
    return [
        f"Every count looks sufficient, but the lessons cannot all be fitted together: at best "
        f"{total} lesson(s) stay unplaced. Short: {short}{more}.",
        "Clashing designated teachers and teachers available on only a few days are the usual "
        "cause. Try removing some designations or adding capacity on more days.",
    ]
