from django.urls import path

from .views import api, builder, dashboard, reports

app_name = "scheduling"

urlpatterns = [
    path("", dashboard.DashboardView.as_view(), name="dashboard"),
    path("timetable/", builder.TimetableIndexView.as_view(), name="timetable"),
    path("timetable/<int:class_id>/", builder.ClassTimetableView.as_view(), name="class_timetable"),
    path("timetable/<int:class_id>/reset/", builder.ResetClassView.as_view(), name="reset_class"),
    path("teachers/", reports.TeacherListView.as_view(), name="teachers"),
    path("teachers/<int:teacher_id>/", reports.TeacherTimetableView.as_view(), name="teacher_timetable"),
    path("workloads/", reports.WorkloadsView.as_view(), name="workloads"),
    path("subjects/", reports.SubjectsView.as_view(), name="subjects"),
    path("requirements/", reports.RequirementsView.as_view(), name="requirements"),
    path("conflicts/", reports.ConflictsView.as_view(), name="conflicts"),
    path("api/slot-options/", api.SlotOptionsView.as_view(), name="slot_options"),
    path("api/lessons/assign/", api.AssignView.as_view(), name="assign_lesson"),
    path("api/lessons/clear/", api.ClearView.as_view(), name="clear_lesson"),
    path("api/lessons/lock/", api.LockView.as_view(), name="lock_lesson"),
    path("api/activities/add/", api.AddActivityView.as_view(), name="add_activity"),
    path("api/activities/remove/", api.RemoveActivityView.as_view(), name="remove_activity"),
]
