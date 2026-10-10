"""Read-side helpers for reviewing a generation run against the live timetable."""

from collections import defaultdict
from dataclasses import dataclass

from school.day_schedule import get_week_schedule
from scheduling.models import TimetableEntry
from scheduling.selectors import Grid, Row, grid_header, row_cells

SAME, KEPT, NEW, CHANGED, REMOVED = "same", "kept", "new", "changed", "removed"
DIFF_LABELS = {
    SAME: "Unchanged",
    KEPT: "Kept",
    NEW: "New",
    CHANGED: "Changed",
    REMOVED: "Removed",
}


def _diff(proposed, current):
    if proposed is None:
        return REMOVED if current is not None else ""
    if proposed.is_kept:
        return KEPT
    if current is None:
        return NEW
    if (current.class_group_id, current.subject_id, current.teacher_id) == (
        proposed.class_group_id,
        proposed.subject_id,
        proposed.teacher_id,
    ):
        return SAME
    return CHANGED


def proposal_grid(run, *, class_group=None, teacher=None):
    """
    The proposal for one class (or one teacher) as rows of cells. Each slot
    cell carries `entry` (the proposed lesson), `current` (the live lesson in
    that slot) and `diff` (new / changed / removed / unchanged / kept).
    """
    week = get_week_schedule()
    width, recess_after, columns = grid_header(week)
    lessons = run.lessons.select_related("class_group__grade", "class_group__section", "subject", "teacher")
    current = TimetableEntry.objects.with_related()
    if class_group is not None:
        lessons, current = lessons.filter(class_group=class_group), current.filter(class_group=class_group)
    else:
        lessons, current = lessons.filter(teacher=teacher), current.filter(teacher=teacher)
    proposed = defaultdict(dict)
    for lesson in lessons:
        proposed[lesson.day][lesson.period] = lesson
    live = defaultdict(dict)
    for entry in current:
        live[entry.day][entry.period] = entry

    rows = []
    for schedule in week:
        cells = row_cells(schedule, width, recess_after, proposed[schedule.day], columns)
        for cell in cells:
            if not cell.is_slot:
                continue
            cell.current = live[schedule.day].get(cell.period)
            cell.diff = _diff(cell.entry, cell.current)
            cell.diff_label = DIFF_LABELS.get(cell.diff, "")
        rows.append(Row(schedule.day, schedule.label, schedule.short_label, cells))
    filled = sum(len(by_period) for by_period in proposed.values())
    return Grid(columns, rows, filled, sum(s.periods for s in week))


@dataclass
class DiffSummary:
    new: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0
    kept: int = 0


def diff_summary(run):
    proposed = {(pl.class_group_id, pl.day, pl.period): pl for pl in run.lessons.all()}
    live = {(e.class_group_id, e.day, e.period): e for e in TimetableEntry.objects.all()}
    summary = DiffSummary()
    for key in proposed.keys() | live.keys():
        diff = _diff(proposed.get(key), live.get(key))
        if diff == SAME:
            summary.unchanged += 1
        elif diff:
            setattr(summary, diff, getattr(summary, diff) + 1)
    return summary
