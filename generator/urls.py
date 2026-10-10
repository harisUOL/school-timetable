from django.urls import path

from . import views

app_name = "generator"

urlpatterns = [
    path("", views.GeneratorIndexView.as_view(), name="index"),
    path("runs/<int:run_id>/", views.RunDetailView.as_view(), name="run_detail"),
    path("runs/<int:run_id>/apply/", views.ApplyRunView.as_view(), name="apply_run"),
]
