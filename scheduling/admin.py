from django.contrib import admin

from .models import TimetableAuditLog, TimetableEntry


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


@admin.register(TimetableAuditLog)
class TimetableAuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit log is written only by the scheduling service."""

    list_display = ["timestamp", "action", "class_label", "day", "period", "user"]
    list_filter = ["action", "day"]
    search_fields = ["class_label", "user__username"]
    readonly_fields = [f.name for f in TimetableAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
