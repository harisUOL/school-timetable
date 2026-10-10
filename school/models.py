"""
School configuration models: the school's structure, staff, and curriculum.

Nothing in this module schedules lessons. Timetable entries live in the
`scheduling` app, and all scheduling rules will live in its service layer.
"""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from .constants import (
    DEFAULT_PERIODS_PER_DAY,
    DEFAULT_RECESS_AFTER_PERIOD,
    MAX_PERIODS_PER_DAY,
    Day,
)

VALID_DAY_Q = Q(day__gte=min(Day.values), day__lte=max(Day.values))


# ==========================================================================
# School calendar configuration
# ==========================================================================


def validate_day_layout(day, periods, recess_after, is_operating):
    """
    Validate an *effective* day layout and protect existing data.

    Shared by SchoolSettings (whose defaults are inherited by days) and
    SchoolDayConfiguration (which can override them), so both enforce the
    same rules. Returns a list of error messages (empty if valid).
    """
    errors = []
    label = Day(day).label

    if is_operating:
        if recess_after and recess_after >= periods:
            errors.append(
                f"{label}: recess after period {recess_after} is not possible "
                f"with only {periods} teaching periods."
            )

    # Changing the layout must not orphan data that already exists.
    TimetableEntry = apps.get_model("scheduling", "TimetableEntry")
    max_allowed = periods if is_operating else 0

    stranded = TimetableEntry.objects.filter(day=day, period__gt=max_allowed).count()
    if stranded:
        errors.append(
            f"{label}: {stranded} timetable lesson(s) are scheduled beyond period "
            f"{max_allowed}. Clear them before reducing this day's periods."
        )

    over_capacity = TeacherDailyAvailability.objects.filter(
        day=day, max_periods__gt=max_allowed
    ).count()
    if over_capacity:
        errors.append(
            f"{label}: {over_capacity} teacher availability record(s) allow more than "
            f"{max_allowed} periods. Lower them before reducing this day's periods."
        )
    return errors


class SchoolSettings(models.Model):
    """
    Singleton holding school-wide defaults. Always stored with pk=1.
    Use `SchoolSettings.load()` rather than querying directly.
    """

    SINGLETON_PK = 1

    school_name = models.CharField(max_length=200, default="School")
    default_periods_per_day = models.PositiveSmallIntegerField(
        default=DEFAULT_PERIODS_PER_DAY,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PERIODS_PER_DAY)],
        help_text="Teaching periods per day, unless a day overrides it.",
    )
    default_recess_after_period = models.PositiveSmallIntegerField(
        default=DEFAULT_RECESS_AFTER_PERIOD,
        validators=[MaxValueValidator(MAX_PERIODS_PER_DAY - 1)],
        help_text="Recess follows this period. Use 0 for no recess.",
    )

    class Meta:
        verbose_name = "school settings"
        verbose_name_plural = "school settings"
        constraints = [
            models.CheckConstraint(condition=Q(pk=1), name="school_settings_singleton"),
        ]

    def __str__(self):
        return f"{self.school_name} settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("School settings cannot be deleted.")

    def clean(self):
        super().clean()
        errors = []
        # Re-validate every day that inherits these defaults.
        for config in SchoolDayConfiguration.objects.all():
            errors.extend(
                validate_day_layout(
                    config.day,
                    config.periods or self.default_periods_per_day,
                    config.recess_after_period
                    if config.recess_after_period is not None
                    else self.default_recess_after_period,
                    config.is_operating,
                )
            )
        if errors:
            raise ValidationError(errors)


class SchoolDayConfiguration(models.Model):
    """
    One row per weekday. A day the school runs on must have a row with
    `is_operating=True`; days with no row are treated as non-operating.

    Blank `periods` / `recess_after_period` inherit from SchoolSettings, so
    e.g. Saturday can have 6 periods while Monday–Friday use the default 9.
    """

    day = models.PositiveSmallIntegerField(choices=Day.choices, unique=True)
    is_operating = models.BooleanField(default=True)
    periods = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PERIODS_PER_DAY)],
        help_text="Leave blank to use the school default.",
    )
    recess_after_period = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_PERIODS_PER_DAY - 1)],
        help_text="Leave blank to use the school default. Use 0 for no recess.",
    )

    class Meta:
        ordering = ["day"]
        verbose_name = "school day"
        constraints = [
            models.CheckConstraint(condition=VALID_DAY_Q, name="school_day_valid_day"),
        ]

    def __str__(self):
        return self.get_day_display()

    def effective_periods(self, settings=None):
        settings = settings or SchoolSettings.load()
        return self.periods or settings.default_periods_per_day

    def effective_recess_after(self, settings=None):
        settings = settings or SchoolSettings.load()
        if self.recess_after_period is not None:
            return self.recess_after_period
        return settings.default_recess_after_period

    def clean(self):
        super().clean()
        if self.day is None:
            return
        settings = SchoolSettings.load()
        errors = validate_day_layout(
            self.day,
            self.effective_periods(settings),
            self.effective_recess_after(settings),
            self.is_operating,
        )
        if errors:
            raise ValidationError(errors)


# ==========================================================================
# School structure
# ==========================================================================


class Grade(models.Model):
    name = models.CharField(max_length=50, help_text='Short form, e.g. "10".')
    order = models.PositiveSmallIntegerField(default=0, help_text="Controls sort order.")

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="grade_name_unique_ci"),
        ]

    def __str__(self):
        return f"Grade {self.name}"


class Section(models.Model):
    name = models.CharField(max_length=20, help_text='e.g. "A".')

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="section_name_unique_ci"),
        ]

    def __str__(self):
        return self.name


class ClassGroupQuerySet(models.QuerySet):
    def with_structure(self):
        return self.select_related("grade", "section")


class ClassGroup(models.Model):
    """A grade + section combination with its own weekly timetable, e.g. 10A."""

    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="class_groups")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="class_groups")

    objects = ClassGroupQuerySet.as_manager()

    class Meta:
        ordering = ["grade__order", "grade__name", "section__name"]
        verbose_name = "class"
        verbose_name_plural = "classes"
        constraints = [
            models.UniqueConstraint(fields=["grade", "section"], name="class_group_unique"),
        ]

    def __str__(self):
        # "10A", but "VII A" and "Sr Nur A" when the grade name ends in a letter.
        separator = " " if self.grade.name[-1:].isalpha() else ""
        return f"{self.grade.name}{separator}{self.section.name}"

    def total_required_periods(self):
        return (
            self.subject_requirements.aggregate(total=models.Sum("periods_per_week"))["total"]
            or 0
        )


# ==========================================================================
# Subjects and staff
# ==========================================================================


class Subject(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(
        max_length=20, null=True, blank=True, unique=True, help_text="Optional, e.g. MATH."
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="subject_name_unique_ci"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Store empty codes as NULL so several subjects can have no code.
        self.code = (self.code or "").strip().upper() or None
        super().save(*args, **kwargs)


class Teacher(models.Model):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    is_cover_teacher = models.BooleanField(
        default=False, help_text="Available to cover absent colleagues."
    )
    subjects = models.ManyToManyField(
        Subject, through="TeacherSubject", related_name="teachers", blank=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def is_qualified_for(self, subject) -> bool:
        subject_id = getattr(subject, "pk", subject)
        return self.teacher_subjects.filter(subject_id=subject_id).exists()

    def capacity_for_day(self, day) -> int:
        """Configured maximum for a day. A missing record means 0 (unavailable)."""
        record = self.daily_availability.filter(day=day).first()
        return record.max_periods if record else 0


class TeacherSubject(models.Model):
    """Which subjects a teacher is qualified to teach (many-to-many)."""

    teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="teacher_subjects"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="teacher_subjects"
    )

    class Meta:
        ordering = ["teacher__name", "subject__name"]
        verbose_name = "teacher subject"
        constraints = [
            models.UniqueConstraint(fields=["teacher", "subject"], name="teacher_subject_unique"),
        ]

    def __str__(self):
        return f"{self.teacher} – {self.subject}"


class TeacherDailyAvailability(models.Model):
    """
    The MAXIMUM number of lessons a teacher can teach on a given day,
    regardless of which periods they fall in. Not a list of free periods.

    A teacher with no record for a day cannot be scheduled that day.
    """

    teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="daily_availability"
    )
    day = models.PositiveSmallIntegerField(choices=Day.choices)
    max_periods = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(MAX_PERIODS_PER_DAY)]
    )

    class Meta:
        ordering = ["teacher__name", "day"]
        verbose_name = "teacher daily capacity"
        verbose_name_plural = "teacher daily capacities"
        constraints = [
            models.UniqueConstraint(fields=["teacher", "day"], name="teacher_day_unique"),
            models.CheckConstraint(condition=VALID_DAY_Q, name="availability_valid_day"),
        ]

    def __str__(self):
        return f"{self.teacher} – {self.get_day_display()}: {self.max_periods}"

    def clean(self):
        super().clean()
        if self.day is None or self.max_periods is None:
            return
        from .day_schedule import get_day_schedule

        schedule = get_day_schedule(self.day)
        if schedule is None:
            if self.max_periods > 0:
                raise ValidationError(
                    {"day": f"The school does not operate on {Day(self.day).label}."}
                )
            return
        if self.max_periods > schedule.periods:
            raise ValidationError(
                {
                    "max_periods": (
                        f"{schedule.label} has only {schedule.periods} teaching periods."
                    )
                }
            )


# ==========================================================================
# Curriculum
# ==========================================================================


class SubjectRequirement(models.Model):
    """
    How many periods of a subject a class receives per week, and optionally
    the designated teacher who normally teaches all of them.
    """

    class_group = models.ForeignKey(
        ClassGroup, on_delete=models.CASCADE, related_name="subject_requirements"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="requirements"
    )
    periods_per_week = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="designated_requirements",
        verbose_name="designated teacher",
        help_text="Optional. Pre-selected when assigning; fixed for automatic generation.",
    )

    class Meta:
        ordering = ["class_group__grade__order", "class_group__section__name", "subject__name"]
        verbose_name = "subject requirement"
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "subject"], name="requirement_class_subject_unique"
            ),
            models.CheckConstraint(
                condition=Q(periods_per_week__gte=1), name="requirement_periods_positive"
            ),
        ]

    def __str__(self):
        return f"{self.class_group} – {self.subject}: {self.periods_per_week}/week"

    def clean(self):
        super().clean()
        errors = {}

        if self.periods_per_week:
            from .day_schedule import weekly_teaching_slots

            slots = weekly_teaching_slots()
            if self.periods_per_week > slots:
                errors["periods_per_week"] = (
                    f"A class has only {slots} teaching periods per week."
                )

        if self.teacher_id and self.subject_id:
            teacher = self.teacher
            if not teacher.is_active:
                errors["teacher"] = f"{teacher} is inactive."
            elif not teacher.is_qualified_for(self.subject_id):
                errors["teacher"] = (
                    f"{teacher} is not qualified to teach {self.subject}. "
                    "Add the subject to the teacher first."
                )

        if errors:
            raise ValidationError(errors)
