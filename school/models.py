"""
School configuration models: the school's structure, staff, and curriculum.

Nothing in this module schedules lessons. Timetable entries live in the
`scheduling` app, and all scheduling rules will live in its service layer.
"""

import datetime

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from .constants import (
    DEFAULT_PERIOD_MINUTES,
    DEFAULT_PERIODS_PER_DAY,
    DEFAULT_RECESS_AFTER_PERIOD,
    DEFAULT_RECESS_MINUTES,
    MAX_PERIOD_MINUTES,
    MAX_PERIODS_PER_DAY,
    MAX_RECESS_MINUTES,
    MIN_PERIOD_MINUTES,
    Day,
)

VALID_DAY_Q = Q(day__gte=min(Day.values), day__lte=max(Day.values))


# ==========================================================================
# School calendar configuration
# ==========================================================================


def period_minutes_validators():
    return [MinValueValidator(MIN_PERIOD_MINUTES), MaxValueValidator(MAX_PERIOD_MINUTES)]


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
    day_start_time = models.TimeField(
        default=datetime.time(8, 0), help_text="When period 1 starts, unless a day overrides it."
    )
    period_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_PERIOD_MINUTES,
        validators=period_minutes_validators(),
        help_text="Length of every period in minutes, unless a day overrides it.",
    )
    recess_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_RECESS_MINUTES,
        validators=[MaxValueValidator(MAX_RECESS_MINUTES)],
        help_text="Length of recess in minutes, unless a day overrides it.",
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
        # Re-validate every day (and every timing group) that inherits these defaults.
        from .day_schedule import Calendar, calendar_errors

        calendar = Calendar.load()
        errors = calendar_errors(calendar.with_changes(settings=self), calendar)
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
    start_time = models.TimeField(
        null=True, blank=True, help_text="When period 1 starts. Leave blank to use the school default."
    )
    period_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=period_minutes_validators(),
        help_text="Period length in minutes. Leave blank to use the school default.",
    )
    recess_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_RECESS_MINUTES)],
        help_text="Recess length in minutes. Leave blank to use the school default.",
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

    def effective_start_time(self, settings=None):
        settings = settings or SchoolSettings.load()
        return self.start_time or settings.day_start_time

    def effective_period_minutes(self, settings=None):
        settings = settings or SchoolSettings.load()
        return self.period_minutes or settings.period_minutes

    def effective_recess_minutes(self, settings=None):
        settings = settings or SchoolSettings.load()
        return self.recess_minutes if self.recess_minutes is not None else settings.recess_minutes

    def clean(self):
        super().clean()
        if self.day is None:
            return
        from .day_schedule import Calendar, calendar_errors

        calendar = Calendar.load()
        errors = calendar_errors(calendar.with_changes(configs={self.day: self}), calendar)
        if errors:
            raise ValidationError(errors)


class TimingGroup(models.Model):
    """
    Grades that keep their own bell times, e.g. "Class I–V" opening at 07:30
    while "Prep" opens at 09:00. A grade with no group uses the school week.

    Each `TimingGroupDay` overrides the school's layout for one weekday; blank
    fields inherit it. Whether the school is open on a day is school-wide.
    """

    name = models.CharField(max_length=100)
    order = models.PositiveSmallIntegerField(default=0, help_text="Controls sort order.")

    class Meta:
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="timing_group_name_unique_ci")]

    def __str__(self):
        return self.name


class TimingGroupDay(models.Model):
    group = models.ForeignKey(TimingGroup, on_delete=models.CASCADE, related_name="days")
    day = models.PositiveSmallIntegerField(choices=Day.choices)
    periods = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PERIODS_PER_DAY)],
        help_text="Leave blank to use the school's value for this day.",
    )
    recess_after_period = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_PERIODS_PER_DAY - 1)],
        help_text="Leave blank to use the school's value. Use 0 for no recess.",
    )
    start_time = models.TimeField(null=True, blank=True, help_text="Leave blank to use the school's value.")
    period_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=period_minutes_validators(), help_text="Leave blank to use the school's value."
    )
    recess_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_RECESS_MINUTES)],
        help_text="Leave blank to use the school's value.",
    )

    class Meta:
        ordering = ["group", "day"]
        verbose_name = "timing group day"
        constraints = [
            models.UniqueConstraint(fields=["group", "day"], name="timing_group_day_unique"),
            models.CheckConstraint(condition=VALID_DAY_Q, name="timing_group_day_valid_day"),
        ]

    def __str__(self):
        return f"{self.group} – {self.get_day_display()}"

    def clean(self):
        super().clean()
        if self.day is None or not self.group_id:
            return
        from .day_schedule import Calendar, calendar_errors

        calendar = Calendar.load()
        days = {**calendar.group_days.get(self.group_id, {}), self.day: self}
        new = calendar.with_changes(group_days={self.group_id: days})
        errors = calendar_errors(new, calendar)
        if errors:
            raise ValidationError(errors)


# ==========================================================================
# School structure
# ==========================================================================


class Grade(models.Model):
    name = models.CharField(max_length=50, help_text='Short form, e.g. "10".')
    order = models.PositiveSmallIntegerField(default=0, help_text="Controls sort order.")
    timing_group = models.ForeignKey(
        TimingGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grades",
        help_text="Grades in a timing group keep its bell times. Blank uses the school week.",
    )

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
        from .day_schedule import Calendar

        calendar = Calendar.load()
        if self.day not in calendar.days:
            if self.max_periods > 0:
                raise ValidationError(
                    {"day": f"The school does not operate on {Day(self.day).label}."}
                )
            return
        longest = calendar.max_periods(self.day)
        if self.max_periods > longest:
            raise ValidationError(
                {"max_periods": f"{Day(self.day).label} has only {longest} teaching periods."}
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

            slots = weekly_teaching_slots(self.class_group_id) if self.class_group_id else weekly_teaching_slots()
            if self.periods_per_week > slots:
                errors["periods_per_week"] = (
                    f"{self.class_group if self.class_group_id else 'A class'} has only {slots} teaching periods per week."
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
