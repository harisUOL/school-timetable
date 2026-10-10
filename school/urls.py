from django.urls import path

from . import views

app_name = "school"

urlpatterns = [
    path("availability/", views.AvailabilityView.as_view(), name="availability"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
]
