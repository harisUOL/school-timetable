from django.urls import path

from .views import dashboard

app_name = "scheduling"

urlpatterns = [
    path("", dashboard.DashboardView.as_view(), name="dashboard"),
]
