"""
The teachers × operating days capacity grid.

One input per cell, named `cap-<teacher id>-<day>`. An empty cell means "no
record", which means the teacher is unavailable that day. Every non-empty
cell is validated through `TeacherDailyAvailability.full_clean()`, and the
whole grid is saved in one transaction or not at all.
"""

import dataclasses
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from .day_schedule import Calendar
from .models import Teacher, TeacherDailyAvailability


def field_name(teacher_id, day):
    return f"cap-{teacher_id}-{day}"


@dataclass
class Cell:
    name: str
    day_label: str
    value: str = ""
    errors: list = field(default_factory=list)

    @property
    def is_empty(self):
        return self.value == ""


@dataclass
class Row:
    teacher: Teacher
    cells: list

    @property
    def weekly_total(self):
        return sum(int(c.value) for c in self.cells if c.value.isdigit() and not c.errors)

    @property
    def has_errors(self):
        return any(c.errors for c in self.cells)


class CapacityGrid:
    def __init__(self, teachers=None):
        # Each day's "max" is the longest day any class has (timing groups differ).
        calendar = Calendar.load()
        self.week = [dataclasses.replace(s, periods=calendar.max_periods(s.day)) for s in calendar.school_week]
        self.teachers = list(
            teachers if teachers is not None else Teacher.objects.filter(is_active=True)
        )
        self.records = {
            (r.teacher_id, r.day): r
            for r in TeacherDailyAvailability.objects.filter(teacher__in=self.teachers)
        }
        self.rows = [
            Row(
                teacher,
                [
                    Cell(
                        field_name(teacher.pk, day.day),
                        day.label,
                        str(self.records[(teacher.pk, day.day)].max_periods)
                        if (teacher.pk, day.day) in self.records
                        else "",
                    )
                    for day in self.week
                ],
            )
            for teacher in self.teachers
        ]

    @property
    def error_count(self):
        return sum(len(c.errors) > 0 for row in self.rows for c in row.cells)

    @property
    def day_totals(self):
        return [
            sum(
                int(row.cells[i].value)
                for row in self.rows
                if row.cells[i].value.isdigit() and not row.cells[i].errors
            )
            for i in range(len(self.week))
        ]

    def bind(self, data):
        """Load submitted values and validate every cell. Returns True if all are valid."""
        self._pending = []
        for row in self.rows:
            for day, cell in zip(self.week, row.cells):
                cell.value = (data.get(cell.name) or "").strip()
                cell.errors = []
                existing = self.records.get((row.teacher.pk, day.day))
                if cell.is_empty:
                    if existing:
                        self._pending.append(("delete", existing))
                    continue
                if not cell.value.isdigit():
                    cell.errors.append("Enter a whole number of lessons, or leave it empty.")
                    continue
                record = existing or TeacherDailyAvailability(teacher=row.teacher, day=day.day)
                new_value = int(cell.value)
                if existing and existing.max_periods == new_value:
                    continue
                record.max_periods = new_value
                try:
                    record.full_clean()
                except ValidationError as exc:
                    cell.errors.extend(exc.messages)
                    continue
                self._pending.append(("save", record))
        return self.error_count == 0

    @transaction.atomic
    def save(self):
        """Apply the changes found by `bind`. Returns the number of records changed."""
        for action, record in self._pending:
            if action == "delete":
                record.delete()
            else:
                record.save()
        return len(self._pending)
