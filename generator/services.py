"""
Running the generator and applying its proposals.

`run_generation()` refuses on data-health errors, solves, explains failures
and stores a complete candidate timetable. It never touches live lessons.

`apply_run()` re-validates EVERY proposed lesson with `scheduling.validators`
against the current configuration, checks the live timetable has not changed
since the run, then writes through `scheduling.services` in one transaction:
clear the unlocked lessons in scope (keep-locked mode), assign each new lesson,
record an audit row. If anything fails, nothing changes.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from school.health import run_health_checks
from school.models import ClassGroup, Subject, Teacher
from scheduling import services as scheduling_services
from scheduling import validators as v
from scheduling.models import TimetableAuditLog, TimetableEntry

from . import diagnose
from .model import Analysis, load_problem, solve
from .models import GenerationRun, ProposedLesson
from .scoring import score

Run = GenerationRun


class ApplyError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def run_generation(*, keep_mode=Run.KeepMode.KEEP_ALL, mode=Run.Mode.COMPLETE, time_limit=None, seed=0, user=None):
    time_limit = int(time_limit or settings.GENERATOR["DEFAULT_TIME_LIMIT"])
    run = GenerationRun.objects.create(
        keep_mode=keep_mode,
        mode=mode,
        time_limit=time_limit,
        random_seed=seed,
        user=user if getattr(user, "is_authenticated", False) else None,
    )
    try:
        _run(run)
    except Exception as exc:  # noqa: BLE001 - recorded on the run, then re-raised
        run.status = Run.Status.FAILED
        run.message = f"The generator failed unexpectedly: {exc}"
        run.save()
        raise
    return run


def _refuse(run, message, reasons):
    run.status = Run.Status.REFUSED
    run.message = message
    run.reasons = reasons
    run.save()


def _run(run):
    errors = [issue.message for issue in run_health_checks() if issue.is_error]
    if errors:
        return _refuse(
            run,
            f"The configuration has {len(errors)} data-health error(s). Fix them on the Data Health "
            "page, then generate again.",
            errors,
        )

    keep_locked = run.keep_mode == Run.KeepMode.KEEP_LOCKED
    problem = load_problem(keep_locked_only=keep_locked)

    broken = _invalid_kept_lessons(problem)
    if broken:
        return _refuse(
            run,
            f"{len(broken)} lesson(s) that would be kept break the current rules. Fix them (see the "
            "conflict report) or choose “Keep locked lessons only”.",
            broken,
        )

    analysis = Analysis(problem)
    best_effort = run.mode == Run.Mode.BEST_EFFORT
    if not best_effort:
        reasons = diagnose.structural_bottlenecks(problem, analysis)
        if reasons:
            run.status = Run.Status.INFEASIBLE
            run.solver_status = "NOT_RUN"
            run.message = "A complete timetable is impossible with the current configuration."
            run.reasons = reasons
            run.save()
            return

    solution = solve(problem, best_effort=best_effort, time_limit=run.time_limit, seed=run.random_seed)
    run.solver_status = solution.status
    run.duration = round(solution.duration, 2)
    run.objective_value = solution.objective

    if not solution.found:
        if solution.status == "INFEASIBLE":
            run.status = Run.Status.INFEASIBLE
            run.message = "No complete timetable exists for the current configuration."
            run.reasons = diagnose.structural_bottlenecks(problem, analysis) or diagnose.best_effort_shortfall(
                problem, time_limit=min(run.time_limit, 20), seed=run.random_seed
            )
        else:
            run.status = Run.Status.FAILED
            run.message = (
                f"No timetable was found within {run.time_limit} seconds. Try a longer time limit "
                "or best-effort mode."
            )
            run.reasons = diagnose.structural_bottlenecks(problem, analysis)
        run.save()
        return

    _store_proposal(run, problem, solution)
    run.metrics = score(run.lessons.all())
    run.metrics["kept"] = len(problem.kept)
    run.metrics["new"] = len(solution.lessons)
    run.reasons = [f"{problem.label(r)}: {missing} lesson(s) not placed." for r, missing in solution.unmet]
    run.status = Run.Status.SUCCEEDED
    quality = "the best possible" if solution.status == "OPTIMAL" else "a good"
    unmet = sum(m for _r, m in solution.unmet)
    run.message = (
        f"Found {quality} timetable: {len(solution.lessons)} new lesson(s) and {len(problem.kept)} kept."
        + (f" {unmet} required lesson(s) could not be placed." if unmet else " Every requirement is met.")
    )
    run.save()


def _invalid_kept_lessons(problem):
    """Kept lessons must still be valid, or the applied timetable would break the rules."""
    if not problem.kept:
        return []
    state = v.MemoryState(problem.kept)
    classes = {c.pk: c for c in ClassGroup.objects.with_structure()}
    subjects = {s.pk: s for s in Subject.objects.all()}
    teachers = {t.pk: t for t in Teacher.objects.all()}
    problems = []
    for lesson in problem.kept:
        for violation in v.validate_assignment(
            classes[lesson.class_id],
            lesson.day,
            lesson.period,
            subjects[lesson.subject_id],
            teachers[lesson.teacher_id],
            exclude_entry_id=lesson.key,
            state=state,
        ):
            problems.append(f"{lesson.class_label} {v.slot_text(lesson.day, lesson.period)}: {violation.message}")
    return problems


def _store_proposal(run, problem, solution):
    rows = [
        ProposedLesson(
            run=run,
            class_group_id=k.class_id,
            day=k.day,
            period=k.period,
            subject_id=k.subject_id,
            teacher_id=k.teacher_id,
            is_kept=True,
            source_entry_id=k.key,
        )
        for k in problem.kept
    ]
    rows += [
        ProposedLesson(
            run=run,
            class_group_id=lesson.class_id,
            day=lesson.day,
            period=lesson.period,
            subject_id=lesson.subject_id,
            teacher_id=lesson.teacher_id,
        )
        for lesson in solution.lessons
    ]
    ProposedLesson.objects.bulk_create(rows)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def validate_proposal(run):
    """Every reason the proposal cannot be applied now (empty list: it can)."""
    proposal = list(run.lessons.select_related("class_group__grade", "class_group__section", "subject", "teacher"))
    problems = []

    # 1. Re-validate every lesson with the same rules as the builder, against
    #    the current configuration, as if the whole proposal were live.
    state = v.MemoryState(
        v.Lesson(
            key=("proposal", pl.pk),
            class_id=pl.class_group_id,
            day=pl.day,
            period=pl.period,
            subject_id=pl.subject_id,
            teacher_id=pl.teacher_id,
            class_label=str(pl.class_group),
            subject_name=pl.subject.name,
            teacher_name=pl.teacher.name,
        )
        for pl in proposal
    )
    for pl in proposal:
        for violation in v.validate_assignment(
            pl.class_group, pl.day, pl.period, pl.subject, pl.teacher, exclude_entry_id=("proposal", pl.pk), state=state
        ):
            problems.append(f"{pl.class_group} {v.slot_text(pl.day, pl.period)}: {violation.message}")

    # 2. The lessons the run kept must still be there, unchanged, and no new
    #    lesson may have appeared in the part of the timetable that is kept.
    live = {e.pk: e for e in TimetableEntry.objects.all()}
    kept_ids = set()
    for pl in proposal:
        if not pl.is_kept:
            continue
        kept_ids.add(pl.source_entry_id)
        entry = live.get(pl.source_entry_id)
        if entry is None or (entry.class_group_id, entry.day, entry.period, entry.subject_id, entry.teacher_id) != (
            pl.class_group_id,
            pl.day,
            pl.period,
            pl.subject_id,
            pl.teacher_id,
        ):
            problems.append(
                f"{pl.class_group} {v.slot_text(pl.day, pl.period)} changed after this run was generated."
            )
    keep_locked = run.keep_mode == Run.KeepMode.KEEP_LOCKED
    for entry in live.values():
        in_scope = entry.is_locked if keep_locked else True
        if in_scope and entry.pk not in kept_ids:
            problems.append(
                f"A lesson was added or locked on {entry.class_group} {v.slot_text(entry.day, entry.period)} "
                "after this run was generated."
            )
    return problems


def apply_run(run, user):
    if run.status != Run.Status.SUCCEEDED:
        raise ApplyError([f"Run #{run.pk} cannot be applied: it is “{run.get_status_display()}”."])
    problems = validate_proposal(run)
    if problems:
        raise ApplyError(["Nothing was changed. Generate a new timetable."] + problems)

    new_lessons = list(
        run.lessons.filter(is_kept=False)
        .select_related("class_group__grade", "class_group__section", "subject", "teacher")
        .order_by("class_group", "day", "period")
    )
    try:
        with transaction.atomic():
            cleared = 0
            if run.keep_mode == Run.KeepMode.KEEP_LOCKED:
                for class_group in ClassGroup.objects.filter(timetable_entries__is_locked=False).distinct():
                    cleared += scheduling_services.reset_class(class_group=class_group, user=user, keep_locked=True)
            for pl in new_lessons:
                scheduling_services.assign_lesson(
                    class_group=pl.class_group,
                    day=pl.day,
                    period=pl.period,
                    subject=pl.subject,
                    teacher=pl.teacher,
                    user=user,
                )
            summary = {"run": run.pk, "cleared": cleared, "assigned": len(new_lessons), "kept": run.lessons.filter(is_kept=True).count()}
            scheduling_services.record_bulk_change(
                action=TimetableAuditLog.Action.GENERATOR_APPLY, user=user, summary=summary
            )
            run.status = Run.Status.APPLIED
            run.applied_at = timezone.now()
            run.applied_by = user if getattr(user, "is_authenticated", False) else None
            run.save(update_fields=["status", "applied_at", "applied_by"])
    except scheduling_services.SchedulingError as exc:
        raise ApplyError(["Nothing was changed."] + [x.message for x in exc.violations]) from None
    return summary
