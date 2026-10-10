from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("print/classes/", views.PrintAllClassesView.as_view(), name="print_classes"),
    path("print/class/<int:class_id>/", views.PrintClassView.as_view(), name="print_class"),
    path("print/teacher/<int:teacher_id>/", views.PrintTeacherView.as_view(), name="print_teacher"),
    path("excel/", views.ExcelView.as_view(), name="excel"),
    path("pdf/class/<int:class_id>/", views.ClassPdfView.as_view(), name="pdf_class"),
    path("pdf/teacher/<int:teacher_id>/", views.TeacherPdfView.as_view(), name="pdf_teacher"),
    path("pdf/school/", views.SchoolPdfView.as_view(), name="pdf_school"),
]
