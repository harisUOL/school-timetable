# Build plan: Phases 2–7

Rules come from `CLAUDE.md`. Each phase ends green on `test`, `makemigrations --check`,
`check` and a smoke run, then is committed as `Phase N: …` with a `docs/CHANGELOG.md` entry.

## Cross-cutting design

- **Roles** (`school/roles.py`): one table of permissions per group. A data migration
  creates "Timetable Editor" and "Viewer"; a `post_migrate` hook adds permissions for
  models introduced later (additive only, never removes an admin's changes).
  Views use `LoginRequiredMixin` everywhere; writes need `scheduling.change_timetableentry`
  (capacity grid: `school.change_teacherdailyavailability`).
- **Layout**: `templates/base.html` with sidebar, one stylesheet (`static/css/app.css`,
  custom properties, light/dark), one JS module per page.
- **Rule engine** (`scheduling/validators.py`): pure functions over a small *state*
  interface. `DatabaseState` reads live rows; `MemoryState` holds a hypothetical
  timetable so the generator can re-validate a whole proposal with the *same* rules.
- **Write path** (`scheduling/services.py`): the only code that writes `TimetableEntry`.
  Atomic, teacher row locks in id order, optimistic concurrency via `expected`
  (`id` + `updated_at`), `IntegrityError` → friendly clash, audit row per change.
- **Read path** (`scheduling/selectors.py`): grids as rows of cells, progress, loads,
  slot options, workloads, conflicts. Templates never do dict lookups.

## Phases

2. `seed_demo` (+`--reset`, idempotent, feasible by construction and asserted by health
   checks), `school/health.py`, base layout + login/logout, role groups, availability grid,
   Data Health page, initial dashboard. Remaining sidebar items shown disabled.
3. Validators, services, `TimetableAuditLog`, selectors, builder views + JSON API,
   `static/js/timetable.js` modal (delegated, focus-trapped), reset confirmation.
4. Workloads, teacher timetable, teachers/subjects lists, requirements overview, conflict
   report (config changed after scheduling), dashboard additions.
5. Styling pass: themes, responsive grid (scrolls in its own container), toasts with
   `aria-live`, button loading states, accessibility. No server rule changes.
6. `generator/` app: `GenerationRun`, `ProposedLesson`, CP-SAT model (`generator/model.py`),
   health pre-check + bottleneck diagnosis, `generate_timetable` command, Generator page,
   review with diff + metrics, apply via services in one transaction.
7. `exports/`: print views, Excel (openpyxl), PDF (reportlab); role enforcement audit
   across every view; README rewrite.
