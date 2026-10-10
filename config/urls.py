from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "School Timetable Administration"
admin.site.site_title = "School Timetable"
admin.site.index_title = "School configuration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("school/", include("school.urls")),
    path("", include("scheduling.urls")),
]
