"""
Timetables as plain text tables, shared by the Excel and PDF exports.

Built from the same selectors as the web pages (`class_grid`, `teacher_grid`,
`teacher_workloads`), so every export shows exactly what the screens show.
"""

from dataclasses import dataclass

from school.models import ClassGroup, SchoolSettings, Teacher
from scheduling import selectors


@dataclass
class TextTable:
    title: str
    subtitle: str
    header: list
    rows: list  # list of lists of str; the first column is the day
    recess_columns: set  # column indexes (in header) that are recess


def _grid_table(title, subtitle, grid, cell_text, extra_header=None, extra_cells=None):
    # The recess column stays narrow and shaded; it needs no heading.
    header = ["Day"] + [c["label"] if c["kind"] != "recess" else "" for c in grid.columns]
    header += [extra_header] if extra_header else []
    recess = {i + 1 for i, c in enumerate(grid.columns) if c["kind"] == "recess"}
    rows = []
    for row in grid.rows:
        cells = [row.label]
        for cell in row.cells:
            if cell.kind == selectors.RECESS:
                cells.append("")
            elif cell.kind == selectors.NONE:
                cells.append("—")
            else:
                cells.append(cell_text(cell.entry) if cell.entry else "")
        if extra_cells:
            cells.append(extra_cells(row))
        rows.append(cells)
    return TextTable(title, subtitle, header, rows, recess)


def class_table(class_group):
    grid = selectors.class_grid(class_group)
    progress = selectors.requirement_progress(class_group)
    return _grid_table(
        f"Class {class_group}",
        f"{grid.filled} lessons · {progress.assigned}/{progress.required} required",
        grid,
        lambda e: f"{e.subject.code or e.subject.name}\n{e.teacher.name}",
    )


def teacher_table(teacher):
    grid = selectors.teacher_grid(teacher)
    subjects = ", ".join(s.name for s in teacher.subjects.all())
    return _grid_table(
        teacher.name,
        f"{subjects} · {grid.filled} lessons, capacity {grid.total_slots}",
        grid,
        lambda e: f"{e.class_group}\n{e.subject.code or e.subject.name}",
        extra_header="Load",
        extra_cells=lambda row: row.load.label,
    )


def workload_table():
    week, rows = selectors.teacher_workloads()
    header = ["Teacher", "Subjects"] + [d.short_label for d in week] + ["Week", "Max", "Remaining"]
    body = [
        [r.teacher.name, ", ".join(r.subjects)] + [c.label for c in r.cells] + [str(r.assigned), str(r.max), str(r.remaining)]
        for r in rows
    ]
    return TextTable("Teacher workloads", "Lessons assigned / daily maximum, across all classes", header, body, set())


def all_classes():
    return list(ClassGroup.objects.with_structure())


def active_teachers():
    return list(Teacher.objects.filter(is_active=True).prefetch_related("subjects"))


def school_name():
    return SchoolSettings.load().school_name
