"""
Generator runs and their proposals.

A run stores its parameters, the solver outcome and a complete candidate
timetable (`ProposedLesson` rows). Proposals never touch live
`TimetableEntry` rows; applying one goes through `scheduling.services`.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from school.constants import Day
from school.models import ClassGroup, Subject, Teacher


class GenerationRun(models.Model):
    class Scope(models.TextChoices):
        SCHOOL = "school", "Whole school"

    class KeepMode(models.TextChoices):
        KEEP_ALL = "keep_all_existing", "Keep all existing lessons and fill the gaps"
        KEEP_LOCKED = "keep_locked_only", "Keep locked lessons only and rebuild the rest"

    class Mode(models.TextChoices):
        COMPLETE = "complete", "Complete: place every required lesson or report why not"
        BEST_EFFORT = "best_effort", "Best effort: place as many lessons as possible"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Proposal ready"
        INFEASIBLE = "infeasible", "No solution"
        REFUSED = "refused", "Refused (data problems)"
        FAILED = "failed", "Failed"
        APPLIED = "applied", "Applied"

    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.SCHOOL)
    keep_mode = models.CharField(max_length=20, choices=KeepMode.choices, default=KeepMode.KEEP_ALL)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.COMPLETE)
    time_limit = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1), MaxValueValidator(600)], help_text="Seconds."
    )
    random_seed = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    solver_status = models.CharField(max_length=30, blank=True)
    objective_value = models.FloatField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True, help_text="Seconds spent solving.")
    message = models.TextField(blank=True)
    reasons = models.JSONField(default=list, blank=True)
    metrics = models.JSONField(default=dict, blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "generation run"

    def __str__(self):
        return f"Run #{self.pk} ({self.get_status_display()})"

    @property
    def mode_label(self):
        return "Best effort" if self.mode == self.Mode.BEST_EFFORT else "Complete"

    @property
    def can_apply(self):
        return self.status == self.Status.SUCCEEDED


class ProposedLesson(models.Model):
    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="lessons")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="+")
    day = models.PositiveSmallIntegerField(choices=Day.choices)
    period = models.PositiveSmallIntegerField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="+")
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="+")
    is_kept = models.BooleanField(default=False, help_text="An existing lesson the run kept.")
    source_entry_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["class_group", "day", "period"]
        constraints = [
            models.UniqueConstraint(fields=["run", "class_group", "day", "period"], name="proposal_class_slot_unique"),
        ]

    def __str__(self):
        return f"{self.class_group} {self.get_day_display()} P{self.period}: {self.subject} ({self.teacher})"
