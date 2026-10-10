"""
Create (or refresh) the demo school described in `school/demo_data.py`.

    python manage.py seed_demo              # create or refresh; safe to re-run
    python manage.py seed_demo --reset      # remove the demo data, then seed afresh
    python manage.py seed_demo --remove     # remove the demo data only
    python manage.py seed_demo --with-users # also create demo_editor / demo_viewer

Only records named in the manifest are touched. Anything else in the
database (your own classes, teachers, subjects…) is left alone.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError

from school import demo_data as demo
from school.constants import Day
from school.day_schedule import get_week_schedule
from school.health import run_health_checks
from school.models import (
    ClassGroup,
    Grade,
    SchoolDayConfiguration,
    Section,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
    TeacherSubject,
)
from school.roles import EDITOR_GROUP, VIEWER_GROUP

DEMO_USERS = [("demo_editor", EDITOR_GROUP), ("demo_viewer", VIEWER_GROUP)]
DEMO_PASSWORD = "demo-timetable"


class Command(BaseCommand):
    help = "Create or refresh the demo school (idempotent). --reset removes demo data first."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Remove the demo data, then seed it again."
        )
        parser.add_argument(
            "--remove", action="store_true", help="Remove the demo data and do not re-seed."
        )
        parser.add_argument(
            "--with-users",
            action="store_true",
            help=f"Also create demo_editor and demo_viewer (password: {DEMO_PASSWORD}).",
        )

    def handle(self, *args, reset=False, remove=False, with_users=False, **options):
        try:
            with transaction.atomic():
                if reset or remove:
                    self.remove_demo_data()
                if not remove:
                    self.seed()
                    if with_users:
                        self.seed_users()
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        if not remove:
            self.report_health()

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def seed(self):
        self.configure_saturday()
        subjects = self.seed_subjects()
        classes = self.seed_classes()
        teachers = self.seed_teachers(subjects)
        self.seed_requirements(classes, subjects, teachers)
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo school ready: {len(classes)} classes, {len(subjects)} subjects, "
                f"{len(teachers)} teachers, "
                f"{SubjectRequirement.objects.filter(class_group__in=classes.values()).count()} "
                "requirements."
            )
        )

    def configure_saturday(self):
        config, _ = SchoolDayConfiguration.objects.get_or_create(day=Day.SATURDAY)
        if config.is_operating and config.periods == demo.SATURDAY_PERIODS:
            return
        config.is_operating = True
        config.periods = demo.SATURDAY_PERIODS
        config.full_clean()
        config.save()

    def seed_subjects(self):
        subjects = {}
        for name, code in demo.SUBJECTS:
            subject = Subject.objects.filter(code=code).first() or Subject.objects.filter(
                name__iexact=name
            ).first()
            if subject is None:
                subject = Subject.objects.create(name=name, code=code)
            subjects[code] = subject
        return subjects

    def seed_classes(self):
        classes = {}
        for name, order in demo.GRADES:
            grade = Grade.objects.filter(name__iexact=name).first()
            if grade is None:
                Grade.objects.create(name=name, order=order)
        for grade_name, section_name in demo.CLASSES:
            grade = Grade.objects.get(name__iexact=grade_name)
            section = Section.objects.filter(name__iexact=section_name).first()
            if section is None:
                section = Section.objects.create(name=section_name)
            class_group, _ = ClassGroup.objects.get_or_create(grade=grade, section=section)
            classes[demo.class_label(grade_name, section_name)] = class_group
        return classes

    def seed_teachers(self, subjects):
        operating = {schedule.day: schedule.periods for schedule in get_week_schedule()}
        teachers = {}
        for name, codes, profile, is_cover in demo.TEACHERS:
            teacher, _ = Teacher.objects.update_or_create(
                name=name, defaults={"is_active": True, "is_cover_teacher": is_cover}
            )
            for code in codes:
                TeacherSubject.objects.get_or_create(teacher=teacher, subject=subjects[code])
            for day, max_periods in demo.PROFILES[profile].items():
                if day not in operating:
                    continue
                TeacherDailyAvailability.objects.update_or_create(
                    teacher=teacher,
                    day=day,
                    defaults={"max_periods": min(max_periods, operating[day])},
                )
            teachers[name] = teacher
        return teachers

    def seed_requirements(self, classes, subjects, teachers):
        weekly = {
            name: sum(t.daily_availability.values_list("max_periods", flat=True))
            for name, t in teachers.items()
        }
        designations = plan_designations(weekly)
        for label, class_group in classes.items():
            grade = label.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            for code, periods in demo.CURRICULUM[grade].items():
                teacher_name = designations.get((label, code))
                requirement = SubjectRequirement.objects.filter(
                    class_group=class_group, subject=subjects[code]
                ).first() or SubjectRequirement(class_group=class_group, subject=subjects[code])
                requirement.periods_per_week = periods
                requirement.teacher = teachers[teacher_name] if teacher_name else None
                requirement.full_clean()
                requirement.save()

    def seed_users(self):
        User = get_user_model()
        for username, group_name in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            user.groups.add(Group.objects.get(name=group_name))
        self.stdout.write(
            f"Demo users: {', '.join(u for u, _ in DEMO_USERS)} (password: {DEMO_PASSWORD})"
        )

    def report_health(self):
        issues = run_health_checks()
        errors = [i for i in issues if i.is_error]
        if errors:
            for issue in errors:
                self.stderr.write(f"  ✗ {issue.message}")
            raise CommandError(f"The demo data has {len(errors)} data-health error(s).")
        self.stdout.write(f"Data health: no errors, {len(issues)} warning(s).")

    # ------------------------------------------------------------------
    # Removal
    # ------------------------------------------------------------------

    def remove_demo_data(self):
        from scheduling.models import TimetableEntry

        class_ids = []
        for grade_name, section_name in demo.CLASSES:
            class_group = ClassGroup.objects.filter(
                grade__name__iexact=grade_name, section__name__iexact=section_name
            ).first()
            if class_group:
                class_ids.append(class_group.pk)
        if TimetableEntry.objects.filter(class_group_id__in=class_ids).exists():
            raise CommandError("The demo classes have lessons. Reset their timetables first.")

        teacher_names = [name for name, *_ in demo.TEACHERS]
        teachers = Teacher.objects.filter(name__in=teacher_names)
        if teachers.filter(timetable_entries__isnull=False).exists():
            raise CommandError(
                "Some demo teachers have lessons in non-demo classes. Clear those lessons first."
            )

        SubjectRequirement.objects.filter(class_group_id__in=class_ids).delete()
        ClassGroup.objects.filter(pk__in=class_ids).delete()
        teacher_count, _ = teachers.delete()

        kept = []
        for name, code in demo.SUBJECTS:
            subject = Subject.objects.filter(code=code, name=name).first()
            if subject is None:
                continue
            try:
                with transaction.atomic():
                    subject.delete()
            except ProtectedError:
                kept.append(name)
        for name, _ in demo.GRADES:
            Grade.objects.filter(name=name, class_groups__isnull=True).delete()
        for section in {s for _, s in demo.CLASSES}:
            Section.objects.filter(name=section, class_groups__isnull=True).delete()

        note = f" Kept subjects still used elsewhere: {', '.join(kept)}." if kept else ""
        self.stdout.write(f"Removed demo data ({len(class_ids)} classes).{note}")


def plan_designations(weekly_capacity):
    """
    Choose a designated teacher for each (class, subject), deterministically.

    Non-cover teachers whose main subject matches are tried first, then other
    qualified non-cover teachers. Within a tier the teacher with the most
    headroom wins. Requirements in demo.UNDESIGNATED are skipped.
    """
    qualified = {}
    for name, codes, _profile, is_cover in demo.TEACHERS:
        if is_cover:
            continue
        for rank, code in enumerate(codes):
            qualified.setdefault(code, []).append((0 if rank == 0 else 1, name))

    load = dict.fromkeys(weekly_capacity, 0)
    limit = {name: int(cap * demo.DESIGNATION_HEADROOM) for name, cap in weekly_capacity.items()}

    demand = {}
    for grade_name, section in demo.CLASSES:
        for code, periods in demo.CURRICULUM[grade_name].items():
            demand[code] = demand.get(code, 0) + periods

    designations = {}
    for code in sorted(demand, key=lambda c: (-demand[c], c)):
        for grade_name, section in demo.CLASSES:
            periods = demo.CURRICULUM[grade_name].get(code)
            label = demo.class_label(grade_name, section)
            if periods is None or (label, code) in demo.UNDESIGNATED:
                continue
            candidates = [
                (tier, -(limit[name] - load[name]), name)
                for tier, name in qualified.get(code, [])
                if load[name] + periods <= limit[name]
            ]
            if not candidates:
                continue
            _, _, chosen = min(candidates)
            load[chosen] += periods
            designations[(label, code)] = chosen
    return designations
