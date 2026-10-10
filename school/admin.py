import dataclasses

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .day_schedule import Calendar, get_day_schedule, get_week_schedule
from .forms import CopyRequirementsForm, CopyTeacherConfigurationForm, FillAvailabilityForm
from .models import (
    ClassGroup,
    Grade,
    SchoolDayConfiguration,
    SchoolSettings,
    Section,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
    TeacherSubject,
    TimingGroup,
    TimingGroupDay,
)

ACTION_FORM_TEMPLATE = "admin/action_form.html"


def render_action_form(modeladmin, request, queryset, form, *, action, title, intro):
    """Intermediate page for admin actions that need extra input."""
    context = {
        **modeladmin.admin_site.each_context(request),
        "title": title,
        "intro": intro,
        "form": form,
        "queryset": queryset,
        "opts": modeladmin.model._meta,
        "action": action,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
    }
    return TemplateResponse(request, ACTION_FORM_TEMPLATE, context)


def initial_selection(request):
    return {"_selected_action": request.POST.getlist(ACTION_CHECKBOX_NAME)}


# ==========================================================================
# School calendar
# ==========================================================================


@admin.register(SchoolSettings)
class SchoolSettingsAdmin(admin.ModelAdmin):
    fieldsets = [
        (None, {"fields": ["school_name"]}),
        (
            "Default day layout",
            {
                "fields": ["default_periods_per_day", "default_recess_after_period"],
                "description": (
                    "Used by every school day that does not override it. "
                    "Recess is not a teaching period and never counts towards workload."
                ),
            },
        ),
        (
            "Default bell times",
            {
                "fields": ["day_start_time", "period_minutes", "recess_minutes"],
                "description": "When period 1 starts and how long periods and recess last. Days may override these.",
            },
        ),
    ]

    def has_add_permission(self, request):
        return not SchoolSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # There is only ever one settings record: go straight to it.
        settings = SchoolSettings.load()
        return HttpResponseRedirect(
            reverse("admin:school_schoolsettings_change", args=[settings.pk])
        )


@admin.register(SchoolDayConfiguration)
class SchoolDayConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "day",
        "is_operating",
        "periods",
        "recess_after_period",
        "start_time",
        "period_minutes",
        "recess_minutes",
        "effective_layout",
    ]
    list_editable = ["is_operating", "periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]
    ordering = ["day"]

    @admin.display(description="Effective layout")
    def effective_layout(self, obj):
        if not obj.is_operating:
            return "Closed"
        settings = SchoolSettings.load()
        periods = obj.effective_periods(settings)
        recess = obj.effective_recess_after(settings)
        recess_text = f"recess after period {recess}" if 0 < recess < periods else "no recess"
        schedule = get_day_schedule(obj.day)
        hours = f", {schedule.hours_label}" if schedule else ""
        return f"{periods} periods, {recess_text}{hours}"


class TimingGroupDayInline(admin.TabularInline):
    model = TimingGroupDay
    extra = 0
    fields = ["day", "periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]
    verbose_name_plural = "days with their own layout (blank fields use the school's)"


@admin.register(TimingGroup)
class TimingGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "grade_list"]
    list_editable = ["order"]
    inlines = [TimingGroupDayInline]

    @admin.display(description="Grades")
    def grade_list(self, obj):
        return ", ".join(g.name for g in obj.grades.all()) or "—"


# ==========================================================================
# School structure
# ==========================================================================


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ["__str__", "name", "order", "timing_group", "class_count"]
    list_editable = ["order", "timing_group"]
    search_fields = ["name"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_class_count=Count("class_groups"))

    @admin.display(description="Classes", ordering="_class_count")
    def class_count(self, obj):
        return obj._class_count


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


class SubjectRequirementInline(admin.TabularInline):
    model = SubjectRequirement
    extra = 0
    fields = ["subject", "periods_per_week", "teacher"]
    autocomplete_fields = ["subject", "teacher"]


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ["__str__", "grade", "section", "subject_count", "required_periods", "load_status"]
    list_filter = ["grade"]
    search_fields = ["grade__name", "section__name"]
    list_select_related = ["grade", "section"]
    inlines = [SubjectRequirementInline]
    actions = ["copy_requirements"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _subject_count=Count("subject_requirements"),
                _required=Sum("subject_requirements__periods_per_week"),
            )
        )

    def changelist_view(self, request, extra_context=None):
        # Computed once per page rather than once per row.
        self._calendar = Calendar.load()
        return super().changelist_view(request, extra_context)

    def _slots(self, obj):
        calendar = getattr(self, "_calendar", None) or Calendar.load()
        return calendar.class_weekly_slots(obj.pk)

    @admin.display(description="Subjects", ordering="_subject_count")
    def subject_count(self, obj):
        return obj._subject_count

    @admin.display(description="Required / week", ordering="_required")
    def required_periods(self, obj):
        return f"{obj._required or 0} / {self._slots(obj)}"

    @admin.display(description="Status")
    def load_status(self, obj):
        required, slots = obj._required or 0, self._slots(obj)
        if required > slots:
            return f"⚠ Over by {required - slots}"
        if required == slots:
            return "Full week"
        return f"{slots - required} free"

    @admin.action(description="Copy subject requirements from another class")
    def copy_requirements(self, request, queryset):
        if "apply" in request.POST:
            form = CopyRequirementsForm(request.POST)
            if form.is_valid():
                source = form.cleaned_data["source"]
                created, updated, skipped = self._copy_requirements(
                    source,
                    queryset.exclude(pk=source.pk),
                    overwrite=form.cleaned_data["overwrite"],
                    include_teacher=form.cleaned_data["include_teacher"],
                )
                self.message_user(
                    request,
                    f"Copied from {source}: {created} added, {updated} updated, "
                    f"{skipped} skipped (already present).",
                    messages.SUCCESS,
                )
                return None
        else:
            form = CopyRequirementsForm(initial=initial_selection(request))
        return render_action_form(
            self,
            request,
            queryset,
            form,
            action="copy_requirements",
            title="Copy subject requirements",
            intro="The source class's subjects and weekly periods will be copied to these classes:",
        )

    @staticmethod
    @transaction.atomic
    def _copy_requirements(source, targets, *, overwrite, include_teacher):
        created = updated = skipped = 0
        source_reqs = list(source.subject_requirements.all())
        for target in targets:
            existing = {r.subject_id: r for r in target.subject_requirements.all()}
            for req in source_reqs:
                current = existing.get(req.subject_id)
                if current is None:
                    SubjectRequirement.objects.create(
                        class_group=target,
                        subject_id=req.subject_id,
                        periods_per_week=req.periods_per_week,
                        teacher_id=req.teacher_id if include_teacher else None,
                    )
                    created += 1
                elif overwrite:
                    current.periods_per_week = req.periods_per_week
                    if include_teacher:
                        current.teacher_id = req.teacher_id
                    current.save()
                    updated += 1
                else:
                    skipped += 1
        return created, updated, skipped


# ==========================================================================
# Subjects and teachers
# ==========================================================================


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "teacher_count"]
    search_fields = ["name", "code"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_teacher_count=Count("teacher_subjects"))

    @admin.display(description="Qualified teachers", ordering="_teacher_count")
    def teacher_count(self, obj):
        return obj._teacher_count


class TeacherSubjectFormSet(BaseInlineFormSet):
    """Stop removing a subject from a teacher who is designated to teach it."""

    def clean(self):
        super().clean()
        for form in self.deleted_forms:
            link = form.instance
            if not link.pk:
                continue
            designated = SubjectRequirement.objects.filter(
                teacher_id=link.teacher_id, subject_id=link.subject_id
            ).select_related("class_group__grade", "class_group__section")
            if designated.exists():
                classes = ", ".join(str(r.class_group) for r in designated)
                raise ValidationError(
                    f"Cannot remove {link.subject}: this teacher is the designated "
                    f"{link.subject} teacher for {classes}. Change those requirements first."
                )


class TeacherSubjectInline(admin.TabularInline):
    model = TeacherSubject
    formset = TeacherSubjectFormSet
    extra = 1
    autocomplete_fields = ["subject"]
    verbose_name = "qualified subject"
    verbose_name_plural = "qualified subjects"


class TeacherDailyAvailabilityInline(admin.TabularInline):
    model = TeacherDailyAvailability
    fields = ["day", "max_periods"]
    verbose_name = "daily capacity"
    verbose_name_plural = "daily capacities (maximum lessons per day; a missing day means unavailable)"

    def get_extra(self, request, obj=None, **kwargs):
        operating = len(get_week_schedule())
        configured = obj.daily_availability.count() if obj else 0
        return max(operating - configured, 0)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "is_active",
        "is_cover_teacher",
        "subject_list",
        "weekly_capacity",
        "days_configured",
    ]
    list_filter = ["is_active", "is_cover_teacher", "subjects"]
    search_fields = ["name"]
    inlines = [TeacherSubjectInline, TeacherDailyAvailabilityInline]
    actions = ["fill_availability", "copy_configuration", "mark_active", "mark_inactive"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _weekly_capacity=Sum("daily_availability__max_periods"),
                _days=Count("daily_availability", distinct=True),
            )
            .prefetch_related("subjects")
        )

    @admin.display(description="Subjects")
    def subject_list(self, obj):
        return ", ".join(s.name for s in obj.subjects.all()) or "—"

    @admin.display(description="Weekly capacity", ordering="_weekly_capacity")
    def weekly_capacity(self, obj):
        return obj._weekly_capacity or 0

    @admin.display(description="Days configured", ordering="_days")
    def days_configured(self, obj):
        return obj._days

    @admin.action(description="Fill daily capacity for all operating days")
    def fill_availability(self, request, queryset):
        if "apply" in request.POST:
            form = FillAvailabilityForm(request.POST)
            if form.is_valid():
                written, capped = self._fill_availability(
                    queryset,
                    form.cleaned_data["max_periods"],
                    overwrite=form.cleaned_data["overwrite"],
                )
                note = f" Capped to the day's length on: {', '.join(sorted(capped))}." if capped else ""
                self.message_user(
                    request, f"Saved {written} daily capacity record(s).{note}", messages.SUCCESS
                )
                return None
        else:
            form = FillAvailabilityForm(initial=initial_selection(request))
        return render_action_form(
            self,
            request,
            queryset,
            form,
            action="fill_availability",
            title="Fill daily capacity",
            intro="Set the maximum lessons per day for these teachers:",
        )

    @staticmethod
    @transaction.atomic
    def _fill_availability(teachers, max_periods, *, overwrite):
        written, capped = 0, set()
        calendar = Calendar.load()
        week = [dataclasses.replace(s, periods=calendar.max_periods(s.day)) for s in calendar.school_week]
        for teacher in teachers:
            existing = set(teacher.daily_availability.values_list("day", flat=True))
            for day in week:
                if day.day in existing and not overwrite:
                    continue
                value = min(max_periods, day.periods)
                if value < max_periods:
                    capped.add(day.label)
                TeacherDailyAvailability.objects.update_or_create(
                    teacher=teacher, day=day.day, defaults={"max_periods": value}
                )
                written += 1
        return written, capped

    @admin.action(description="Copy subjects/capacities from another teacher")
    def copy_configuration(self, request, queryset):
        if "apply" in request.POST:
            form = CopyTeacherConfigurationForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                source = data["source"]
                targets = queryset.exclude(pk=source.pk)
                self._copy_configuration(
                    source,
                    targets,
                    copy_subjects=data["copy_subjects"],
                    copy_availability=data["copy_availability"],
                    overwrite=data["overwrite_availability"],
                )
                self.message_user(
                    request,
                    f"Copied configuration from {source} to {targets.count()} teacher(s).",
                    messages.SUCCESS,
                )
                return None
        else:
            form = CopyTeacherConfigurationForm(initial=initial_selection(request))
        return render_action_form(
            self,
            request,
            queryset,
            form,
            action="copy_configuration",
            title="Copy teacher configuration",
            intro="Configuration will be copied to these teachers:",
        )

    @staticmethod
    @transaction.atomic
    def _copy_configuration(source, targets, *, copy_subjects, copy_availability, overwrite):
        subject_ids = list(source.teacher_subjects.values_list("subject_id", flat=True))
        capacities = list(source.daily_availability.values_list("day", "max_periods"))
        for target in targets:
            if copy_subjects:
                TeacherSubject.objects.bulk_create(
                    [TeacherSubject(teacher=target, subject_id=sid) for sid in subject_ids],
                    ignore_conflicts=True,
                )
            if copy_availability:
                existing = set(target.daily_availability.values_list("day", flat=True))
                for day, max_periods in capacities:
                    if day in existing and not overwrite:
                        continue
                    TeacherDailyAvailability.objects.update_or_create(
                        teacher=target, day=day, defaults={"max_periods": max_periods}
                    )

    @admin.action(description="Mark selected teachers as active")
    def mark_active(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} teacher(s) marked active.", messages.SUCCESS)

    @admin.action(description="Mark selected teachers as inactive")
    def mark_inactive(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} teacher(s) marked inactive.", messages.SUCCESS)


# ==========================================================================
# Curriculum and capacities (flat list views for quick bulk editing)
# ==========================================================================


@admin.register(SubjectRequirement)
class SubjectRequirementAdmin(admin.ModelAdmin):
    list_display = ["class_group", "subject", "periods_per_week", "teacher"]
    list_editable = ["periods_per_week"]
    list_filter = ["class_group__grade", "class_group", "subject"]
    search_fields = ["subject__name", "teacher__name"]
    list_select_related = ["class_group__grade", "class_group__section", "subject", "teacher"]
    autocomplete_fields = ["subject", "teacher"]


@admin.register(TeacherDailyAvailability)
class TeacherDailyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["teacher", "day", "max_periods"]
    list_editable = ["max_periods"]
    list_filter = ["day", "teacher"]
    search_fields = ["teacher__name"]
    list_select_related = ["teacher"]
    autocomplete_fields = ["teacher"]
