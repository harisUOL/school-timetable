"""
Timetable data.

IMPORTANT: Do not create, change or delete TimetableEntry rows directly from
views, forms, admin or scripts. From Phase 3 onwards every write goes through
`scheduling.services`, the single authoritative assignment path used by both
the manual builder and the automatic generator. The database constraints
below are the last line of defence, not the primary validation.

Extensibility note: an entry is currently "one class, one teacher, one
subject, one slot". Combined or team-taught lessons can later be modelled by
introducing a Lesson that owns several class/teacher links, without changing
how the service layer is called.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from school.constants import Day
from school.models import VALID_DAY_Q, ClassGroup, Subject, Teacher


class TimetableEntryQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related(
            "class_group__grade", "class_group__section", "teacher", "subject"
        )

    def for_class(self, class_group):
        return self.filter(class_group=class_group)

    def for_teacher(self, teacher):
        return self.filter(teacher=teacher)


class TimetableEntry(models.Model):
    class_group = models.ForeignKey(
        ClassGroup, on_delete=models.CASCADE, related_name="timetable_entries"
    )
    day = models.PositiveSmallIntegerField(choices=Day.choices)
    period = models.PositiveSmallIntegerField()
    # PROTECT: a teacher or subject with scheduled lessons cannot be deleted.
    # Deactivate the teacher instead, or clear their lessons first.
    teacher = models.ForeignKey(
        Teacher, on_delete=models.PROTECT, related_name="timetable_entries"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="timetable_entries"
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked lessons are kept as they are by the automatic generator.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = TimetableEntryQuerySet.as_manager()

    class Meta:
        ordering = ["class_group", "day", "period"]
        verbose_name = "timetable entry"
        verbose_name_plural = "timetable entries"
        constraints = [
            # A class has at most one lesson per slot.
            models.UniqueConstraint(
                fields=["class_group", "day", "period"], name="entry_class_slot_unique"
            ),
            # A teacher is in at most one class per slot, across the WHOLE school.
            # This also protects against two admins assigning at the same moment.
            models.UniqueConstraint(
                fields=["teacher", "day", "period"], name="entry_teacher_slot_unique"
            ),
            models.CheckConstraint(condition=VALID_DAY_Q, name="entry_valid_day"),
            models.CheckConstraint(condition=Q(period__gte=1), name="entry_period_positive"),
        ]
        indexes = [
            # Daily workload counts: "how many lessons does teacher T have on day D?"
            models.Index(fields=["teacher", "day"], name="entry_teacher_day_idx"),
            # Requirement progress: "how many Maths lessons does 10A have?"
            models.Index(fields=["class_group", "subject"], name="entry_class_subject_idx"),
        ]

    def __str__(self):
        return (
            f"{self.class_group} · {self.get_day_display()} P{self.period} · "
            f"{self.subject} ({self.teacher})"
        )

    def clean(self):
        """Single-row checks only. Cross-table rules live in the service layer."""
        super().clean()
        if self.day is None or self.period is None:
            return
        from school.day_schedule import get_day_schedule

        schedule = get_day_schedule(self.day)
        if schedule is None:
            raise ValidationError({"day": f"The school does not operate on {Day(self.day).label}."})
        if not schedule.is_valid_period(self.period):
            raise ValidationError(
                {"period": f"{schedule.label} has teaching periods 1–{schedule.periods} only."}
            )


class TimetableAuditLog(models.Model):
    """
    One row per change made through `scheduling.services`. `before`/`after`
    hold entry snapshots (a list of snapshots for a class reset). Never edited.
    """

    class Action(models.TextChoices):
        ASSIGN = "assign", "Assign"
        REPLACE = "replace", "Replace"
        CLEAR = "clear", "Clear"
        RESET = "reset", "Reset class"
        LOCK = "lock", "Lock"
        UNLOCK = "unlock", "Unlock"
        GENERATOR_APPLY = "generator_apply", "Apply generated timetable"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    class_group = models.ForeignKey(
        ClassGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    class_label = models.CharField(max_length=50, blank=True)
    day = models.PositiveSmallIntegerField(choices=Day.choices, null=True, blank=True)
    period = models.PositiveSmallIntegerField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "timetable audit log entry"
        verbose_name_plural = "timetable audit log"

    def __str__(self):
        where = f" {self.get_day_display()} P{self.period}" if self.day is not None else ""
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.get_action_display()} {self.class_label}{where}"
