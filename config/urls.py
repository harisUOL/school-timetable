from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "School Timetable Administration"
admin.site.site_title = "School Timetable"
admin.site.index_title = "School configuration"

urlpatterns = [
    path("admin/", admin.site.urls),
    # Until the timetable builder exists (Phase 3), send visitors to the admin.
    path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),
]
