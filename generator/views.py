from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from school.health import run_health_checks
from school.models import ClassGroup, Teacher
from school.permissions import RolePermissionMixin
from school.roles import GENERATOR_RUN_PERMISSION, TIMETABLE_WRITE_PERMISSION
from scheduling.models import TimetableEntry

from . import selectors, services
from .forms import GenerationForm
from .models import GenerationRun
from .scoring import comparable, compare, score

READ_PERMISSION = "generator.view_generationrun"


class GeneratorIndexView(RolePermissionMixin, TemplateView):
    """GET: parameters, data health and recent runs. POST: run the solver (synchronously)."""

    template_name = "generator/index.html"
    permission_required = READ_PERMISSION
    write_permission = GENERATOR_RUN_PERMISSION

    def get_context_data(self, form=None, **kwargs):
        issues = run_health_checks()
        errors = [i for i in issues if i.is_error]
        return super().get_context_data(
            form=form or GenerationForm(),
            errors=errors,
            warning_count=len(issues) - len(errors),
            runs=GenerationRun.objects.select_related("user")[:10],
            can_run=self.can_write(),
            lesson_count=TimetableEntry.objects.count(),
            locked_count=TimetableEntry.objects.filter(is_locked=True).count(),
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        form = GenerationForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        data = form.cleaned_data
        run = services.run_generation(
            keep_mode=data["keep_mode"],
            mode=data["mode"],
            time_limit=data["time_limit"],
            seed=data["random_seed"],
            user=request.user,
        )
        level = messages.SUCCESS if run.status == GenerationRun.Status.SUCCEEDED else messages.ERROR
        messages.add_message(request, level, run.message)
        return redirect("generator:run_detail", run_id=run.pk)


class RunDetailView(RolePermissionMixin, TemplateView):
    """Review a run: outcome, metrics against the current timetable, and the diff per class or teacher."""

    template_name = "generator/run_detail.html"
    permission_required = READ_PERMISSION

    def get_context_data(self, **kwargs):
        run = get_object_or_404(GenerationRun.objects.select_related("user", "applied_by"), pk=kwargs["run_id"])
        context = {"run": run, "can_apply": run.can_apply and self.request.user.has_perms(
            [TIMETABLE_WRITE_PERMISSION, "generator.change_generationrun"]
        )}
        if run.lessons.exists():
            classes = list(ClassGroup.objects.with_structure())
            teachers = list(Teacher.objects.filter(is_active=True))
            view = "teacher" if self.request.GET.get("view") == "teacher" else "class"
            selected_class = selected_teacher = None
            if view == "teacher":
                selected_teacher = _pick(teachers, self.request.GET.get("teacher"))
                grid = selectors.proposal_grid(run, teacher=selected_teacher) if selected_teacher else None
            else:
                selected_class = _pick(classes, self.request.GET.get("class"))
                grid = selectors.proposal_grid(run, class_group=selected_class) if selected_class else None
            current = score(TimetableEntry.objects.all())
            context.update(
                view=view,
                classes=classes,
                teachers=teachers,
                selected_class=selected_class,
                selected_teacher=selected_teacher,
                grid=grid,
                diff=selectors.diff_summary(run),
                metrics=compare(run.metrics, current) if run.metrics else [],
                comparable=comparable(run.metrics, current) if run.metrics else True,
                legend=list(selectors.DIFF_LABELS.items()),
            )
        return super().get_context_data(**context, **kwargs)


def _pick(objects, raw_id):
    if raw_id and raw_id.isdigit():
        for obj in objects:
            if obj.pk == int(raw_id):
                return obj
    return objects[0] if objects else None


class ApplyRunView(RolePermissionMixin, View):
    """POST only: apply a proposal through the scheduling service, all or nothing."""

    permission_required = (READ_PERMISSION, "generator.change_generationrun")
    write_permission = TIMETABLE_WRITE_PERMISSION

    def post(self, request, run_id):
        run = get_object_or_404(GenerationRun, pk=run_id)
        try:
            summary = services.apply_run(run, request.user)
        except services.ApplyError as exc:
            for problem in exc.problems[:10]:
                messages.error(request, problem)
            if len(exc.problems) > 10:
                messages.error(request, f"…and {len(exc.problems) - 10} more problem(s).")
            return redirect("generator:run_detail", run_id=run.pk)
        messages.success(
            request,
            f"Applied run #{run.pk}: {summary['assigned']} lesson(s) assigned, {summary['cleared']} "
            f"cleared, {summary['kept']} kept.",
        )
        return redirect("scheduling:dashboard")
