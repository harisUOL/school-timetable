from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import include, path
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    """For the host's health check: no login, no data, just "the app is up"."""
    return HttpResponse("ok", content_type="text/plain")


admin.site.site_header = "School Timetable Administration"
admin.site.site_title = "School Timetable"
admin.site.index_title = "School configuration"

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("school/", include("school.urls")),
    path("generator/", include("generator.urls")),
    path("exports/", include("exports.urls")),
    path("", include("scheduling.urls")),
]
