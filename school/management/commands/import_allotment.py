"""
Import classes, subjects, teachers and weekly requirements from an allotment workbook.

    python manage.py import_allotment "TT Allotment 25-26.xlsx"
    python manage.py import_allotment allotment.xlsx --dry-run          # show what would happen
    python manage.py import_allotment allotment.xlsx --report skipped.csv

Safe to re-run with an updated sheet: records are matched by name/code, existing
daily capacities are kept (new teachers get the full day on every operating day),
and each imported class's requirements are brought in line with the sheet.

The school week (days, periods, bell times) is not touched: set it on the
Settings page, preferably before importing so new teachers get the right full day.

Allocations the system cannot represent yet (combined classes and parallel
groups, see school/allotment.py) are skipped and listed.
"""

import csv

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from school.allotment import REASONS, AllotmentError, grade_order, parse_allotment, plan_import, split_class
from school.day_schedule import Calendar
from school.health import run_health_checks
from school.models import (
    ClassGroup,
    Grade,
    Section,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
    TeacherSubject,
)

class DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Import an allotment workbook (Teacher / Class / Subject / Periods per week)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The .xlsx file with an “Allotment” sheet.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, then roll back.")
        parser.add_argument("--report", help="Also write the skipped allocations to this CSV file.")

    def handle(self, *args, path, dry_run=False, report=None, **options):
        try:
            plan = plan_import(parse_allotment(path))
        except AllotmentError as exc:
            raise CommandError(str(exc)) from exc

        try:
            with transaction.atomic():
                counts = self.apply(plan)
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would import' if dry_run else 'Imported'}: {counts['classes']} classes, "
                f"{counts['subjects']} subjects, {counts['teachers']} teachers, "
                f"{counts['requirements']} requirements ({counts['periods']} periods a week)."
            )
        )
        if counts["removed"]:
            self.stdout.write(f"Removed {counts['removed']} requirement(s) no longer in the sheet.")
        self.report_skipped(plan, report)
        if not dry_run:
            self.report_health()

    # ------------------------------------------------------------------

    def apply(self, plan):
        calendar = Calendar.load()

        subjects = {}
        for code, name in plan.subjects.items():
            subject = (
                Subject.objects.filter(code=code.upper()).first()
                or Subject.objects.filter(name__iexact=name).first()
                or Subject.objects.create(name=name, code=code)
            )
            subjects[code] = subject

        classes = {}
        for label in plan.classes:
            grade_name, section_name = split_class(label)
            grade = Grade.objects.filter(name__iexact=grade_name).first() or Grade.objects.create(
                name=grade_name, order=grade_order(grade_name)
            )
            section = Section.objects.filter(name__iexact=section_name).first() or Section.objects.create(
                name=section_name
            )
            classes[label], _ = ClassGroup.objects.get_or_create(grade=grade, section=section)

        teachers = {}
        for name in plan.teachers:
            teacher = Teacher.objects.filter(name__iexact=name).first() or Teacher.objects.create(name=name)
            if not teacher.is_active:
                teacher.is_active = True
                teacher.save(update_fields=["is_active"])
            for day in calendar.days:
                # Never overwrite a capacity someone has already refined. A full day is
                # the longest day any class has (timing groups may differ).
                TeacherDailyAvailability.objects.get_or_create(
                    teacher=teacher, day=day, defaults={"max_periods": calendar.max_periods(day)}
                )
            teachers[name] = teacher

        for teacher_name, code in plan.qualifications:
            TeacherSubject.objects.get_or_create(teacher=teachers[teacher_name], subject=subjects[code])

        wanted = set()
        periods_total = 0
        for (label, code), (periods, teacher_name) in plan.requirements.items():
            class_group, subject = classes[label], subjects[code]
            requirement = SubjectRequirement.objects.filter(class_group=class_group, subject=subject).first()
            requirement = requirement or SubjectRequirement(class_group=class_group, subject=subject)
            requirement.periods_per_week = periods
            requirement.teacher = teachers[teacher_name]
            requirement.full_clean()
            requirement.save()
            wanted.add(requirement.pk)
            periods_total += periods

        stale = SubjectRequirement.objects.filter(class_group__in=classes.values()).exclude(pk__in=wanted)
        removed, _ = stale.delete()
        return {
            "classes": len(classes),
            "subjects": len(subjects),
            "teachers": len(teachers),
            "requirements": len(wanted),
            "periods": periods_total,
            "removed": removed,
        }

    # ------------------------------------------------------------------

    def report_skipped(self, plan, path):
        if not plan.skipped:
            return
        self.stdout.write(
            self.style.WARNING(f"\nSkipped {len(plan.skipped)} allocation(s) the timetable cannot represent yet:")
        )
        for reason, text in REASONS.items():
            groups = {}
            for a, r in plan.skipped:
                if r != reason:
                    continue
                key = (a.subject, a.classes, a.periods) if reason == "combined" else (a.subject, a.classes)
                groups.setdefault(key, []).append(a)
            if not groups:
                continue
            self.stdout.write(f"\n  {text.capitalize()}:")
            for items in groups.values():
                a = items[0]
                classes = ", ".join(a.classes) if len(a.classes) <= 4 else f"{len(a.classes)} classes ({', '.join(sorted(a.classes))})"
                who = ", ".join(f"{i.teacher} ({i.periods})" for i in items) if len(items) <= 4 else f"{len(items)} teachers"
                self.stdout.write(f"    {a.subject} for {classes}: {who}")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Row", "Teacher", "Classes", "Subject", "Periods/week", "Reason"])
                for a, reason in plan.skipped:
                    writer.writerow([a.row, a.teacher, ", ".join(a.classes), a.subject, a.periods, REASONS[reason]])
            self.stdout.write(f"\nSkipped allocations written to {path}")

    def report_health(self):
        issues = run_health_checks()
        errors = [i for i in issues if i.is_error]
        self.stdout.write(f"\nData health: {len(errors)} error(s), {len(issues) - len(errors)} warning(s).")
        for issue in issues[:15]:
            self.stdout.write(f"  [{issue.severity}] {issue.message}")
        if len(issues) > 15:
            self.stdout.write(f"  …and {len(issues) - 15} more on the Data Health page.")
