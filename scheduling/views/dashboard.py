from django.views.generic import TemplateView

from school.health import run_health_checks
from school.models import ClassGroup, Subject, SubjectRequirement, Teacher
from school.permissions import RolePermissionMixin

from .. import selectors


class DashboardView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/dashboard.html"
    permission_required = "scheduling.view_timetableentry"

    def get_context_data(self, **kwargs):
        issues = run_health_checks()
        return super().get_context_data(
            counts=[
                ("Classes", ClassGroup.objects.count()),
                ("Active teachers", Teacher.objects.filter(is_active=True).count()),
                ("Subjects", Subject.objects.count()),
                ("Requirements", SubjectRequirement.objects.count()),
            ],
            completion=selectors.school_completion(),
            top_issues=issues[:5],
            issue_count=len(issues),
            error_count=sum(i.is_error for i in issues),
            **kwargs,
        )
