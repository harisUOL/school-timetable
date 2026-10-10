from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView

from .availability import CapacityGrid
from .day_schedule import get_week_schedule, weekly_teaching_slots
from .health import group_issues, run_health_checks
from .models import SchoolSettings
from .permissions import RolePermissionMixin
from .roles import CAPACITY_WRITE_PERMISSION


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


class SettingsView(RolePermissionMixin, TemplateView):
    """The school week and links into the admin. Administrators (superusers) only."""

    template_name = "school/settings.html"
    permission_required = "school.view_schoolsettings"

    def has_permission(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            school=SchoolSettings.load(),
            week=get_week_schedule(),
            weekly_slots=weekly_teaching_slots(),
            **kwargs,
        )
