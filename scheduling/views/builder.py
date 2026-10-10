"""Timetable builder pages: class selector, class grid and reset confirmation."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import TemplateView

from school.models import ClassGroup, Grade
from school.permissions import RolePermissionMixin
from school.roles import TIMETABLE_WRITE_PERMISSION

from .. import selectors, services

READ_PERMISSION = "scheduling.view_timetableentry"


def class_selector_context(selected_class=None, selected_grade_id=None):
    grades = list(Grade.objects.prefetch_related("class_groups__section", "class_groups__grade"))
    if selected_class is not None:
        selected_grade_id = selected_class.grade_id
    current = next((g for g in grades if str(g.pk) == str(selected_grade_id)), None)
    return {
        "grades": grades,
        "current_grade": current,
        "grade_classes": list(current.class_groups.all()) if current else [],
        "current_class": selected_class,
    }


class TimetableIndexView(RolePermissionMixin, TemplateView):
    """GET /timetable/: pick a grade, then a class. ?class=<id> opens that class."""

    template_name = "scheduling/timetable.html"
    permission_required = READ_PERMISSION

    def get(self, request, *args, **kwargs):
        class_id = request.GET.get("class")
        if class_id and class_id.isdigit() and ClassGroup.objects.filter(pk=class_id).exists():
            return redirect("scheduling:class_timetable", class_id=int(class_id))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        grade = self.request.GET.get("grade")
        if grade is None:
            first = Grade.objects.first()
            grade = first.pk if first else None
        return super().get_context_data(**class_selector_context(None, grade), **kwargs)


class ClassTimetableView(RolePermissionMixin, TemplateView):
    template_name = "scheduling/timetable.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=kwargs["class_id"])
        return super().get_context_data(
            **class_selector_context(class_group),
            **class_page_context(class_group),
            can_edit=self.request.user.has_perm(TIMETABLE_WRITE_PERMISSION),
            **kwargs,
        )


def class_page_context(class_group):
    grid = selectors.class_grid(class_group)
    return {
        "class_group": class_group,
        "grid": grid,
        "progress": selectors.requirement_progress(class_group),
    }


class ResetClassView(RolePermissionMixin, TemplateView):
    """GET shows a confirmation; POST removes the class's lessons."""

    template_name = "scheduling/reset_confirm.html"
    # Even the confirmation page is for editors only.
    permission_required = (READ_PERMISSION, TIMETABLE_WRITE_PERMISSION)
    write_permission = TIMETABLE_WRITE_PERMISSION

    def get_context_data(self, **kwargs):
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=kwargs["class_id"])
        entries = class_group.timetable_entries.all()
        return super().get_context_data(
            class_group=class_group,
            lesson_count=entries.count(),
            locked_count=entries.filter(is_locked=True).count(),
            **kwargs,
        )

    def post(self, request, class_id):
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=class_id)
        keep_locked = request.POST.get("keep_locked") == "on"
        removed = services.reset_class(
            class_group=class_group, user=request.user, keep_locked=keep_locked
        )
        kept = " Locked lessons were kept." if keep_locked else ""
        messages.success(request, f"Removed {removed} lesson(s) from {class_group}.{kept}")
        return redirect(reverse("scheduling:class_timetable", args=[class_group.pk]))
