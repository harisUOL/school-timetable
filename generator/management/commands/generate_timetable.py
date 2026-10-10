"""
Generate a whole-school timetable with CP-SAT and (by default) apply it.

    python manage.py generate_timetable                    # keep existing lessons, fill the rest, apply
    python manage.py generate_timetable --keep locked      # rebuild everything except locked lessons
    python manage.py generate_timetable --best-effort      # place as many lessons as possible
    python manage.py generate_timetable --no-apply         # only store the proposal for review
    python manage.py generate_timetable --time-limit 120 --seed 7
"""

from django.core.management.base import BaseCommand, CommandError

from generator.models import GenerationRun
from generator.services import ApplyError, apply_run, run_generation


class Command(BaseCommand):
    help = "Generate a school timetable with OR-Tools CP-SAT and apply it through the scheduling service."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            choices=["all", "locked"],
            default="all",
            help="all: keep every existing lesson (default). locked: keep only locked lessons and "
            "replace the rest.",
        )
        parser.add_argument("--best-effort", action="store_true", help="Place as many lessons as possible.")
        parser.add_argument("--time-limit", type=int, default=None, help="Solver time limit in seconds.")
        parser.add_argument("--seed", type=int, default=0, help="Random seed for the solver.")
        parser.add_argument("--no-apply", action="store_true", help="Store the proposal without applying it.")

    def handle(self, *args, keep, best_effort, time_limit, seed, no_apply, **options):
        if time_limit is not None and not 1 <= time_limit <= 600:
            raise CommandError("--time-limit must be between 1 and 600 seconds.")
        keep_mode = GenerationRun.KeepMode.KEEP_LOCKED if keep == "locked" else GenerationRun.KeepMode.KEEP_ALL
        mode = GenerationRun.Mode.BEST_EFFORT if best_effort else GenerationRun.Mode.COMPLETE
        self.stdout.write("Generating… (this can take up to the time limit)")
        run = run_generation(keep_mode=keep_mode, mode=mode, time_limit=time_limit, seed=seed)

        self.stdout.write(f"Run #{run.pk}: {run.get_status_display()} [{run.solver_status or 'not solved'}]")
        if run.duration is not None:
            self.stdout.write(f"Solved in {run.duration:.1f}s")
        self.stdout.write(run.message)
        for reason in run.reasons:
            self.stdout.write(f"  - {reason}")

        if run.status != GenerationRun.Status.SUCCEEDED:
            raise CommandError("No timetable was generated.")
        metrics = run.metrics
        self.stdout.write(
            f"Lessons placed: {metrics['placed']}/{metrics['required']} · daily-limit excess: "
            f"{metrics['subject_daily_excess']} · teacher gaps: {metrics['teacher_gaps']} · long runs: "
            f"{metrics['long_runs']}"
        )
        if no_apply:
            self.stdout.write("Proposal stored. Review and apply it on the Generator page.")
            return
        try:
            summary = apply_run(run, user=None)
        except ApplyError as exc:
            raise CommandError("Could not apply: " + "; ".join(exc.problems)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied: {summary['assigned']} lesson(s) assigned, {summary['cleared']} cleared, "
                f"{summary['kept']} kept."
            )
        )
