"""Read-only pages: workloads, teachers, subjects, requirements and conflicts."""

from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView

from school.models import ClassGroup, Grade, Subject, Teacher
from school.permissions import RolePermissionMixin

from .. import selectors

READ_PERMISSION = "scheduling.view_timetableentry"


def _int_param(request, name):
    value = request.GET.get(name, "")
    return int(value) if value.isdigit() else None


class WorkloadsView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/workloads.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        subject_id = _int_param(self.request, "subject")
        sort = self.request.GET.get("sort", "name")
        if sort not in selectors.WORKLOAD_SORTS:
            sort = "name"
        week, rows = selectors.teacher_workloads(subject_id=subject_id, sort=sort)
        return super().get_context_data(
            week=week,
            rows=rows,
            subjects=Subject.objects.all(),
            subject_id=subject_id,
            sort=sort,
            sorts=[(key, label) for key, (label, _) in selectors.WORKLOAD_SORTS.items()],
            legend=list(selectors.LOAD_STATE_LABELS.items()),
            **kwargs,
        )


class TeacherListView(RolePermissionMixin, TemplateView):
    """Lists teachers. ?teacher=<id> (the picker on a teacher's page) opens that teacher."""

    template_name = "scheduling/teachers.html"
    permission_required = READ_PERMISSION

    def get(self, request, *args, **kwargs):
        teacher_id = _int_param(request, "teacher")
        if teacher_id and Teacher.objects.filter(pk=teacher_id).exists():
            return redirect("scheduling:teacher_timetable", teacher_id=teacher_id)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(rows=selectors.teacher_summaries(), **kwargs)


class TeacherTimetableView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/teacher_timetable.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        teacher = get_object_or_404(Teacher.objects.prefetch_related("subjects"), pk=kwargs["teacher_id"])
        grid = selectors.teacher_grid(teacher)
        return super().get_context_data(
            teacher=teacher,
            grid=grid,
            teachers=Teacher.objects.filter(is_active=True),
            **kwargs,
        )


class SubjectsView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/subjects.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        return super().get_context_data(rows=selectors.subject_summaries(), **kwargs)


class RequirementsView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/requirements.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        grade_id = _int_param(self.request, "grade")
        class_id = _int_param(self.request, "class")
        rows = selectors.requirements_overview(grade_id=grade_id, class_id=class_id)
        return super().get_context_data(
            rows=rows,
            grades=Grade.objects.all(),
            classes=ClassGroup.objects.with_structure(),
            grade_id=grade_id,
            class_id=class_id,
            differs_count=sum(r.differs for r in rows),
            conflict_count=selectors.conflict_count(),
            **kwargs,
        )


class ConflictsView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/conflicts.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        groups = selectors.find_conflicts()
        return super().get_context_data(
            groups=groups, total=selectors.conflict_count(groups), **kwargs
        )
