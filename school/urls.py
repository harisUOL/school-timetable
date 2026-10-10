from django.urls import path

from . import views

app_name = "school"

urlpatterns = [
    path("availability/", views.AvailabilityView.as_view(), name="availability"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/timing-groups/new/", views.TimingGroupView.as_view(), name="timing_group_new"),
    path("settings/timing-groups/<int:pk>/", views.TimingGroupView.as_view(), name="timing_group_edit"),
    path("settings/timing-groups/<int:pk>/delete/", views.TimingGroupDeleteView.as_view(), name="timing_group_delete"),
]
