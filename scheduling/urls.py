from django.urls import path

from .views import api, builder, dashboard

app_name = "scheduling"

urlpatterns = [
    path("", dashboard.DashboardView.as_view(), name="dashboard"),
    path("timetable/", builder.TimetableIndexView.as_view(), name="timetable"),
    path("timetable/<int:class_id>/", builder.ClassTimetableView.as_view(), name="class_timetable"),
    path("timetable/<int:class_id>/reset/", builder.ResetClassView.as_view(), name="reset_class"),
    path("api/slot-options/", api.SlotOptionsView.as_view(), name="slot_options"),
    path("api/lessons/assign/", api.AssignView.as_view(), name="assign_lesson"),
    path("api/lessons/clear/", api.ClearView.as_view(), name="clear_lesson"),
    path("api/lessons/lock/", api.LockView.as_view(), name="lock_lesson"),
]
