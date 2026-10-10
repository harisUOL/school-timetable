"""
JSON endpoints used by static/js/timetable.js. One endpoint per operation.

Views only parse input and render results: every rule is checked by
`scheduling.services` / `scheduling.validators`. Successful writes return the
re-rendered cell, the progress panel, header stats and any warnings; failures
return 400 with the violations.
"""

import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views import View

from school.day_schedule import get_day_schedule
from school.models import ClassGroup, Subject, Teacher
from school.permissions import RolePermissionMixin
from school.roles import TIMETABLE_WRITE_PERMISSION

from .. import selectors, services
from .. import validators as v
from ..models import TimetableEntry

READ_PERMISSION = "scheduling.view_timetableentry"


class BadRequest(Exception):
    pass


def error_response(violations, status=400):
    return JsonResponse(
        {"ok": False, "violations": [x.as_dict() for x in violations]}, status=status
    )


def bad_request(message):
    return error_response([v.Violation("BAD_REQUEST", message)])


def _int(data, key):
    value = data.get(key)
    if isinstance(value, bool):
        raise BadRequest(f"'{key}' must be a whole number.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadRequest(f"'{key}' must be a whole number.") from None


def _expected(data):
    expected = data.get("expected")
    if expected is None:
        return None
    if not isinstance(expected, dict) or "id" not in expected or "updated_at" not in expected:
        raise BadRequest("'expected' must contain the lesson's id and updated_at.")
    return {"id": expected["id"], "updated_at": str(expected["updated_at"])}


class ApiMixin(RolePermissionMixin):
    permission_required = READ_PERMISSION
    write_permission = TIMETABLE_WRITE_PERMISSION

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return error_response(
                [v.Violation("PERMISSION_DENIED", "You do not have permission to change timetables.")],
                status=403,
            )
        return super().handle_no_permission()


class SlotOptionsView(ApiMixin, View):
    """GET /api/slot-options/?class=&day=&period="""

    def get(self, request):
        try:
            class_id = _int(request.GET, "class")
            day = _int(request.GET, "day")
            period = _int(request.GET, "period")
        except BadRequest as exc:
            return bad_request(str(exc))
        class_group = get_object_or_404(ClassGroup.objects.with_structure(), pk=class_id)
        schedule = get_day_schedule(day, class_group)
        if schedule is None or not schedule.is_valid_period(period):
            return error_response(
                [v.Violation(v.INVALID_SLOT, "That is not a teaching slot.", {"day": day, "period": period})]
            )
        return JsonResponse({"ok": True, **selectors.slot_options(class_group, day, period)})


class WriteView(ApiMixin, View):
    """Parses the JSON body, calls `perform`, and renders the shared response."""

    def post(self, request):
        try:
            data = json.loads(request.body or b"{}")
            if not isinstance(data, dict):
                raise BadRequest("Send a JSON object.")
            class_group = ClassGroup.objects.with_structure().filter(pk=_int(data, "class_id")).first()
            if class_group is None:
                raise BadRequest("That class does not exist.")
            day, period = _int(data, "day"), _int(data, "period")
            result = self.perform(request, data, class_group, day, period)
        except json.JSONDecodeError:
            return bad_request("The request body is not valid JSON.")
        except BadRequest as exc:
            return bad_request(str(exc))
        except services.SchedulingError as exc:
            return error_response(exc.violations)
        return JsonResponse(self.render_result(request, class_group, day, period, result))

    def perform(self, request, data, class_group, day, period):
        raise NotImplementedError

    def render_result(self, request, class_group, day, period, result):
        progress = selectors.requirement_progress(class_group)
        grid = selectors.class_grid(class_group)
        cell = selectors.class_cell(class_group, day, period)
        context = {"cell": cell, "progress": progress, "grid": grid, "can_edit": True}
        return {
            "ok": True,
            "action": result.action,
            "cell_html": render_to_string("scheduling/includes/cell.html", context, request),
            "progress_html": render_to_string("scheduling/includes/progress.html", context, request),
            "stats": {
                "filled": grid.filled,
                "total_slots": grid.total_slots,
                "required": progress.required,
                "assigned": progress.assigned,
            },
            "warnings": [w.as_dict() for w in result.warnings],
            "message": self.success_message(result, class_group, day, period),
        }

    def success_message(self, result, class_group, day, period):
        where = f"{class_group} {v.slot_text(day, period)}"
        if result.entry is None:
            return f"Removed the lesson from {where}."
        entry = result.entry
        verbs = {"assign": "Assigned", "replace": "Changed", "lock": "Locked", "unlock": "Unlocked"}
        extra = " as an extra lesson" if entry.is_extra and result.action in ("assign", "replace") else ""
        return f"{verbs.get(result.action, 'Saved')} {entry.subject} with {entry.teacher} on {where}{extra}."


class AssignView(WriteView):
    """POST /api/lessons/assign/ {class_id, day, period, subject_id, teacher_id, extra?, expected?}"""

    def perform(self, request, data, class_group, day, period):
        if not isinstance(data.get("extra", False), bool):
            raise BadRequest("'extra' must be true or false.")
        subject = Subject.objects.filter(pk=_int(data, "subject_id")).first()
        teacher = Teacher.objects.filter(pk=_int(data, "teacher_id")).first()
        if subject is None or teacher is None:
            raise BadRequest("Choose a subject and a teacher that exist.")
        return services.assign_lesson(
            class_group=class_group,
            day=day,
            period=period,
            subject=subject,
            teacher=teacher,
            user=request.user,
            expected=_expected(data),
            extra=data.get("extra", False),
        )


class ClearView(WriteView):
    """POST /api/lessons/clear/ {class_id, day, period, expected}"""

    def perform(self, request, data, class_group, day, period):
        return services.clear_lesson(
            class_group=class_group, day=day, period=period, user=request.user, expected=_expected(data)
        )


class LockView(WriteView):
    """POST /api/lessons/lock/ {class_id, day, period, locked, expected}"""

    def perform(self, request, data, class_group, day, period):
        if not isinstance(data.get("locked"), bool):
            raise BadRequest("'locked' must be true or false.")
        expected = _expected(data)
        entry = TimetableEntry.objects.filter(class_group=class_group, day=day, period=period).first()
        if entry is None:
            raise services.SchedulingError(
                [v.Violation(v.SLOT_EMPTY, f"{class_group} has no lesson on {v.slot_text(day, period)}.")]
            )
        return services.set_locked(entry=entry, locked=data["locked"], user=request.user, expected=expected)


class ActivityView(WriteView):
    """
    Activities can change many cells and classes at once, so a success adds a
    message for the next page and asks the client to reload the timetable.
    """

    def render_result(self, request, class_group, day, period, result):
        messages.success(request, self.success_message(result, class_group, day, period))
        return {"ok": True, "reload": True}


def _classes_text(classes):
    names = [str(c) for c in classes]
    return names[0] if len(names) == 1 else f"{len(names)} classes"


class AddActivityView(ActivityView):
    """POST /api/activities/add/ {class_id, day, period, label, length, scope, clear_lessons}"""

    def perform(self, request, data, class_group, day, period):
        scope = data.get("scope", "class")
        if scope not in services.ACTIVITY_SCOPES:
            raise BadRequest("Choose which classes the activity is for.")
        if not isinstance(data.get("clear_lessons", False), bool):
            raise BadRequest("'clear_lessons' must be true or false.")
        return services.add_activity(
            class_group=class_group,
            day=day,
            start=period,
            length=_int(data, "length"),
            label=str(data.get("label", "")),
            scope=scope,
            clear_lessons=data.get("clear_lessons", False),
            user=request.user,
        )

    def success_message(self, result, class_group, day, period):
        message = f"Added {result.label} on {services._slot_list(day, result.periods)} for {_classes_text(result.classes)}."
        if result.cleared:
            message += f" {result.cleared} lesson(s) were removed to make room; generate with “Keep all existing lessons” to place them again."
        if result.skipped:
            message += f" Skipped {', '.join(str(c) for c in result.skipped)}: their day has no such period."
        return message


class RemoveActivityView(ActivityView):
    """POST /api/activities/remove/ {class_id, day, period, everywhere}"""

    def perform(self, request, data, class_group, day, period):
        if not isinstance(data.get("everywhere", False), bool):
            raise BadRequest("'everywhere' must be true or false.")
        return services.remove_activity(
            class_group=class_group, day=day, period=period, everywhere=data.get("everywhere", False), user=request.user
        )

    def success_message(self, result, class_group, day, period):
        return f"Removed {result.label} from {_classes_text(result.classes)}."
