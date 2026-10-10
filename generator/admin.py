from django.contrib import admin

from .models import GenerationRun


@admin.register(GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    """Read-only: runs are created by the Generator page and the generate_timetable command."""

    list_display = ["id", "created_at", "status", "solver_status", "keep_mode", "mode", "duration", "user"]
    list_filter = ["status", "keep_mode", "mode"]
    readonly_fields = [f.name for f in GenerationRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
