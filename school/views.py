from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from .availability import CapacityGrid
from .day_schedule import SCHOOL_WEEK, Calendar
from .health import group_issues, run_health_checks
from .models import Grade, SchoolSettings, TimingGroup
from .permissions import RolePermissionMixin
from .roles import CAPACITY_WRITE_PERMISSION
from .week_settings import TimingGroupForm, WeekSettingsForm, delete_group, delete_group_errors


class AvailabilityView(RolePermissionMixin, TemplateView):
    template_name = "school/availability.html"
    permission_required = "school.view_teacherdailyavailability"
    write_permission = CAPACITY_WRITE_PERMISSION

    def get_context_data(self, grid=None, **kwargs):
        grid = grid or CapacityGrid()
        return super().get_context_data(
            grid=grid, week=grid.week, can_edit=self.can_write(), **kwargs
        )

    def post(self, request, *args, **kwargs):
        grid = CapacityGrid()
        if not grid.bind(request.POST):
            messages.error(
                request,
                f"Nothing was saved: {grid.error_count} cell(s) need attention.",
            )
            return self.render_to_response(self.get_context_data(grid=grid), status=400)
        changed = grid.save()
        if changed:
            messages.success(request, f"Saved daily capacities ({changed} change(s)).")
        else:
            messages.info(request, "No changes to save.")
        return redirect("school:availability")


class HealthView(RolePermissionMixin, TemplateView):
    template_name = "school/health.html"
    permission_required = "school.view_subjectrequirement"

    def get_context_data(self, **kwargs):
        issues = run_health_checks()
        return super().get_context_data(
            groups=group_issues(issues),
            error_count=sum(i.is_error for i in issues),
            warning_count=sum(not i.is_error for i in issues),
            **kwargs,
        )


class AdministratorOnlyMixin(RolePermissionMixin):
    """The school's calendar is configuration: administrators (superusers) only."""

    permission_required = "school.view_schoolsettings"

    def has_permission(self):
        return self.request.user.is_superuser


def _capacity_note(changed):
    if not changed:
        return ""
    return f" {changed} teacher capacit{'y was' if changed == 1 else 'ies were'} adjusted to fit."


def schedule_summaries(calendar):
    """Each bell schedule with the grades that follow it, for the Settings page."""
    grades = list(Grade.objects.order_by("order", "name"))
    result = []
    for key, name, week in calendar.schedules():
        members = [g.name for g in grades if (calendar.grade_group.get(g.pk) or SCHOOL_WEEK) == key]
        if key is SCHOOL_WEEK and calendar.groups and not members:
            continue  # every grade has its own group: the school week only sets defaults
        result.append(
            {
                "key": key,
                "name": name if key is not SCHOOL_WEEK else ("Other grades" if calendar.groups else "All grades"),
                "grades": members,
                "week": week,
                "weekly_slots": sum(s.periods for s in week),
            }
        )
    return result


class SettingsView(AdministratorOnlyMixin, TemplateView):
    """The school week, timing groups and bell times."""

    template_name = "school/settings.html"

    def get_context_data(self, form=None, **kwargs):
        calendar = Calendar.load()
        return super().get_context_data(
            school=SchoolSettings.load(),
            schedules=schedule_summaries(calendar),
            groups=TimingGroup.objects.prefetch_related("grades"),
            form=form or WeekSettingsForm(),
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        form = WeekSettingsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Nothing was saved: " + " ".join(form.errors))
            return self.render_to_response(self.get_context_data(form=form), status=400)
        changed = form.save()
        messages.success(request, "Saved the school week and bell times." + _capacity_note(changed))
        return redirect("school:settings")


class TimingGroupView(AdministratorOnlyMixin, TemplateView):
    """Create (no pk) or edit a timing group."""

    template_name = "school/timing_group.html"

    def get_group(self):
        pk = self.kwargs.get("pk")
        return get_object_or_404(TimingGroup, pk=pk) if pk else None

    def get_context_data(self, form=None, **kwargs):
        group = self.get_group()
        return super().get_context_data(group=group, form=form or TimingGroupForm(group=group), **kwargs)

    def post(self, request, *args, **kwargs):
        group = self.get_group()
        form = TimingGroupForm(request.POST, group=group)
        if not form.is_valid():
            messages.error(request, "Nothing was saved: " + " ".join(form.errors))
            return self.render_to_response(self.get_context_data(form=form), status=400)
        group, changed = form.save()
        messages.success(request, f"Saved the timing group “{group}”." + _capacity_note(changed))
        return redirect("school:settings")


class TimingGroupDeleteView(AdministratorOnlyMixin, View):
    def post(self, request, pk):
        group = get_object_or_404(TimingGroup, pk=pk)
        errors = delete_group_errors(group)
        if errors:
            messages.error(request, f"“{group}” was not deleted: " + " ".join(errors))
            return redirect("school:timing_group_edit", pk=pk)
        name = str(group)
        changed = delete_group(group)
        messages.success(
            request, f"Deleted the timing group “{name}”. Its grades now follow the school week." + _capacity_note(changed)
        )
        return redirect("school:settings")
