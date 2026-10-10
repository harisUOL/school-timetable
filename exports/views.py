"""
Exports: print pages, Excel and PDF. Viewers and editors may export; every
view requires login and the timetable view permission.
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from school.models import ClassGroup, Teacher
from school.permissions import RolePermissionMixin
from scheduling import selectors

from . import excel, pdf, tables

READ_PERMISSION = "scheduling.view_timetableentry"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(content, content_type, filename):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _stamp():
    return timezone.localtime().strftime("%Y%m%d")


# --------------------------------------------------------------------------
# Print pages
# --------------------------------------------------------------------------


class PrintView(RolePermissionMixin, TemplateView):
    template_name = "exports/print.html"
    permission_required = READ_PERMISSION

    def pages(self):
        raise NotImplementedError

    def back_url(self):
        return reverse("scheduling:timetable")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            pages=self.pages(),
            back_url=self.back_url(),
            school_name=tables.school_name(),
            printed=timezone.localtime(),
            **kwargs,
        )


class PrintClassView(PrintView):
    def pages(self):
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=self.kwargs["class_id"])
        return [_class_page(class_group)]

    def back_url(self):
        return reverse("scheduling:class_timetable", args=[self.kwargs["class_id"]])


class PrintAllClassesView(PrintView):
    def pages(self):
        return [_class_page(c) for c in ClassGroup.objects.with_structure()]


class PrintTeacherView(PrintView):
    def back_url(self):
        return reverse("scheduling:teacher_timetable", args=[self.kwargs["teacher_id"]])

    def pages(self):
        teacher = get_object_or_404(Teacher.objects.prefetch_related("subjects"), pk=self.kwargs["teacher_id"])
        grid = selectors.teacher_grid(teacher)
        subjects = ", ".join(s.name for s in teacher.subjects.all())
        return [{"kind": "teacher", "title": teacher.name, "subtitle": subjects, "grid": grid}]


def _class_page(class_group):
    grid = selectors.class_grid(class_group)
    progress = selectors.requirement_progress(class_group)
    return {
        "kind": "class",
        "title": f"Class {class_group}",
        "subtitle": f"{progress.assigned} of {progress.required} required lessons",
        "grid": grid,
    }


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


class ExcelView(RolePermissionMixin, View):
    permission_required = READ_PERMISSION

    def get(self, request):
        return _download(excel.build_workbook(), XLSX, f"timetable-{_stamp()}.xlsx")


class ClassPdfView(RolePermissionMixin, View):
    permission_required = READ_PERMISSION

    def get(self, request, class_id):
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=class_id)
        return _download(pdf.class_pdf(class_group), "application/pdf", f"class-{class_group}-{_stamp()}.pdf")


class TeacherPdfView(RolePermissionMixin, View):
    permission_required = READ_PERMISSION

    def get(self, request, teacher_id):
        teacher = get_object_or_404(Teacher.objects.prefetch_related("subjects"), pk=teacher_id)
        slug = "-".join(teacher.name.lower().split())
        return _download(pdf.teacher_pdf(teacher), "application/pdf", f"teacher-{slug}-{_stamp()}.pdf")


class SchoolPdfView(RolePermissionMixin, View):
    permission_required = READ_PERMISSION

    def get(self, request):
        return _download(pdf.school_pdf(), "application/pdf", f"school-timetable-{_stamp()}.pdf")
