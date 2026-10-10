"""Read-side helpers for reviewing a generation run against the live timetable."""

from collections import defaultdict
from dataclasses import dataclass

from school.day_schedule import Calendar
from scheduling.models import TimetableEntry
from scheduling.selectors import LESSON, MIXED_NOTE, Cell, Grid, Row, agenda, grid_header, row_cells, teacher_week

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
    calendar = Calendar.load()
    lessons = run.lessons.select_related("class_group__grade", "class_group__section", "subject", "teacher")
    current = TimetableEntry.objects.with_related()
    if class_group is not None:
        lessons, current = lessons.filter(class_group=class_group), current.filter(class_group=class_group)
        week = calendar.class_week(class_group.pk)
    else:
        lessons, current = list(lessons.filter(teacher=teacher)), current.filter(teacher=teacher)
        week = teacher_week(calendar, {pl.class_group_id for pl in lessons} | {e.class_group_id for e in current})
        if week is None:
            return _teacher_agenda(calendar, lessons, current)
    width, recess_after, columns = grid_header(week)
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


def _teacher_agenda(calendar, lessons, current):
    """A teacher across timing groups: each day's proposed and removed lessons in time order."""
    proposed = {(pl.class_group_id, pl.day, pl.period): pl for pl in lessons}
    live = {(e.class_group_id, e.day, e.period): e for e in current}
    by_day = defaultdict(list)
    for key in proposed.keys() | live.keys():
        class_id, day, period = key
        by_day[day].append((class_id, period, (proposed.get(key), live.get(key))))

    def make_cell(s, period, pair):
        entry, live_entry = pair
        cell = Cell(LESSON, s.day, s.label, period, entry)
        cell.current = live_entry
        cell.diff = _diff(entry, live_entry)
        cell.diff_label = DIFF_LABELS.get(cell.diff, "")
        return cell

    columns, agenda_rows = agenda(calendar, by_day, make_cell)
    rows = [Row(s.day, s.label, s.short_label, cells) for s, cells in agenda_rows]
    return Grid(columns, rows, len(lessons), sum(len(v) for v in by_day.values()), MIXED_NOTE)


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
