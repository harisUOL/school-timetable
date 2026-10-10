from django.contrib import admin

from .models import TimetableEntry


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    """
    Read-only on purpose.

    Lessons must be created, changed and removed through the scheduling
    service (the timetable builder from Phase 3), which enforces teacher
    capacity, qualifications and subject requirements. Allowing edits here
    would open a second write path that skips those rules.
    """

    list_display = ["class_group", "day", "period", "subject", "teacher", "is_locked", "updated_at"]
    list_filter = ["day", "class_group__grade", "class_group", "teacher", "subject", "is_locked"]
    search_fields = ["teacher__name", "subject__name"]
    list_select_related = ["class_group__grade", "class_group__section", "teacher", "subject"]
    ordering = ["class_group__grade__order", "class_group__section__name", "day", "period"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
