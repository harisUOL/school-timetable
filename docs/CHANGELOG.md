# Changelog

## Phase 2: School data management and demo data

**Built**
- `manage.py seed_demo` creates:
  - grades 9–12 with ten classes;
  - 12 subjects, each with a code;
  - 27 teachers: most teach 2–3 subjects, and 3 are cover teachers;
  - five daily-capacity profiles;
  - Saturday set to 6 periods, giving 51 weekly slots;
  - 116 requirements (49 or 48 periods per class), 107 of them with a designated teacher.

  It is idempotent, and it finishes by running the health checks, failing if any error
  is found. Options:
  - `--reset` removes the demo data, then seeds it afresh.
  - `--remove` only removes it.
  - `--with-users` creates `demo_editor` and `demo_viewer` (password `demo-timetable`).
- `school/health.py` runs the eight requested checks. Each check returns `Issue`s with a
  severity, a message naming the people and classes involved, and an admin link to fix it.
- Base layout (`templates/base.html`, a sidebar, `static/css/app.css`) and login/logout
  pages. Sidebar items for pages that do not exist yet are disabled automatically.
- Role groups "Timetable Editor" and "Viewer":
  - created by data migration `school/0003`;
  - permissions defined in `school/roles.py`;
  - kept in sync after each `migrate`, and the sync only ever adds permissions.
- New pages:
  - Availability grid (`/school/availability/`)
  - Data Health (`/school/health/`)
  - Settings (`/school/settings/`)
  - an initial Dashboard (`/`)

**Decisions**
- `CLAUDE.md` is copied into the repository root so the rules live with the code.
- `--reset` re-seeds after removing; `--remove` only removes.
  - Demo records are identified by the manifest's natural keys: class labels, teacher
    names and subject codes.
  - Subjects still used by non-demo data are kept.
  - The Saturday layout is school configuration, so removal leaves it alone.
- "Designated lessons on a class cannot fit daily capacities":
  - Capacities are already capped at each day's length, so a plain fit test would
    duplicate the weekly-overload check.
  - The check therefore also applies the spread rule: at most 2 lessons of a subject per
    class per day (`TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY`).
  - Because that rule is soft, this issue is a **warning**.
  - "Capacity above day length" and "no capacity records" are also warnings. Every other
    check is an error.
- Permissions on the new pages:
  - Editors may edit daily capacities (`school.change_teacherdailyavailability`);
    Viewers cannot.
  - Users with no role get 403 on every page.
  - Admin "fix" links are shown only to superusers, since only they can open the admin.
- The availability grid lists active teachers. It validates every cell and saves all or
  nothing; on any error it returns 400 and highlights the bad cells.

**Try it**
```bash
python manage.py migrate
python manage.py seed_demo --with-users
python manage.py createsuperuser
python manage.py runserver   # sign in at /accounts/login/
```

## Phase 3: Manual timetable builder

**Built**
- `scheduling/validators.py`:
  - `validate_assignment(...)` returns `Violation(code, message, context)`.
  - `assignment_warnings(...)` returns the non-blocking `DESIGNATED_TEACHER_MISMATCH`.
  - The rules run over a state interface. `DatabaseState` uses targeted queries;
    `MemoryState` holds a snapshot or a hypothetical timetable for the modal and the
    generator. The rule code is the same either way.
- `scheduling/services.py` provides `assign_lesson`, `clear_lesson`, `reset_class`,
  `set_locked` and `SchedulingError`. Every call:
  - is atomic;
  - locks teacher rows in id order;
  - checks optimistic concurrency (`expected` = id + `updated_at`, giving `STALE_SLOT`);
  - maps `IntegrityError` to a friendly clash;
  - writes an audit row.
- `TimetableAuditLog` model, read-only in the admin.
- `scheduling/selectors.py` provides `class_grid`, `class_cell`, `requirement_progress`,
  `teacher_day_load` and `slot_options`.
- Pages and endpoints:
  - Pages: `/timetable/` (grade → class pills) and `/timetable/<id>/` (header stats,
    grid, requirements panel).
  - Reset confirmation: `/timetable/<id>/reset/` (option to keep locked lessons).
  - JSON API: `/api/slot-options/` and `/api/lessons/{assign,clear,lock}/`.
- `static/js/timetable.js`:
  - one delegated click listener and a native `<dialog>`;
  - focus trap and Escape to close;
  - the designated teacher is pre-selected, and choosing someone else shows a warning;
  - server errors appear inside the modal, with "Reload this slot" when the slot is stale.
- `seed_demo --remove` now clears demo lessons through `reset_class` and no longer refuses.

**Decisions**
- Extra codes:
  - `SLOT_OCCUPIED`: assigning to a filled slot without `expected`. This is the
    "duplicate slot without replace" case.
  - `SLOT_EMPTY`: clearing or locking an empty slot.
  - Neither weakens a listed rule.
- Replacing a locked lesson keeps its lock, and reset can drop or keep locked lessons.
  Locks only bind the generator, as the model's help text says.
- When day layouts differ:
  - The header follows the longest day that has a recess.
  - A day with a different recess position still shows recess at its own position.
  - Each cell carries its own Roman numeral, so a row never misleads.
- Write endpoints return 403 as JSON (`PERMISSION_DENIED`) for signed-in users without
  the permission. Anonymous users are redirected to login, like every page.
- Viewers see a read-only grid: no buttons, no JS module, no reset.

**Try it**: open *Class Timetables*, pick a grade and a class, and click any slot.

## Phase 4: Workload and requirement tracking

**Built**
- **Teacher Workloads** (`/workloads/`): teachers × operating days as `assigned/max`.
  - Weekly totals and remaining capacity.
  - Colour states with text labels: free, near full, full, over capacity, no capacity.
  - Filter by subject; sort by name, highest load or most remaining.
- **Teacher timetable** (`/teachers/<id>/`):
  - the teacher's week across all classes, with load per day;
  - each lesson links to its slot in the class timetable.
- **Teachers** (`/teachers/`): subjects, designated load, assigned/capacity and status.
  **Subjects** (`/subjects/`): qualified teachers, demand, lessons placed and capacity.
- **Subject Requirements** (`/requirements/`): every class × subject.
  - Columns: required, assigned, remaining and progress, the designated teacher, who
    actually teaches it, and a "Differs" flag.
  - Filters by grade and class.
- **Conflict report** (`/conflicts/`):
  - Re-checks every live lesson with the same validators the builder uses, against the
    current configuration.
  - Reports deactivated teachers, removed qualifications, removed requirements,
    requirements reduced below the lessons placed, capacities lowered below the
    lessons placed, and removed capacity records.
  - Each conflict links to its slot(s) (`/timetable/<class>/#slot-<day>-<period>`),
    and the linked cell is highlighted.
- Dashboard: completion per class, the five most-loaded teachers, and the conflict count.

**Decisions**
- Capacity and requirement overruns are reported once per teacher-day or class-subject,
  listing every involved slot. No single lesson is "the" wrong one, so the person
  chooses which to move.
- "Near full" means one lesson left or at least 75 % used.
- The Teachers page doubles as the target of the teacher picker (`?teacher=<id>`), so
  no inline JavaScript is needed.

**Try it**: assign some lessons. Then lower a teacher's capacity in *Availability* and
open *Subject Requirements → Conflict report*.

## Phase 5: UI refinement

**Built**
- Colours:
  - Every colour is a CSS custom property.
  - The light and dark themes follow the system setting, and the sidebar
    "Dark/Light theme" button overrides it (remembered per browser).
  - `static/js/theme.js` applies the saved theme before first paint.
- Responsive layout:
  - At tablet width and below, the sidebar becomes a sticky top bar with a *Menu* button
    (`aria-expanded`).
  - On phones the timetable keeps its size and scrolls inside its own box. Checked in
    headless Chrome at 390 px and 820 px: no page-level horizontal scroll on any main page.
- Toasts (`static/js/app.js`):
  - Django messages and in-page actions appear as toasts in `aria-live` regions.
  - Success and info toasts are polite; errors are assertive and persist until dismissed.
- Busy states:
  - POST forms disable their submit buttons and show a spinner while submitting, which
    blocks double submits.
  - The timetable modal disables its actions and marks the grid `aria-busy` while a
    request is in flight.
- Accessibility:
  - visible `:focus-visible` rings everywhere;
  - a skip link;
  - labelled controls and progress bars;
  - colour states always paired with text;
  - `prefers-reduced-motion` respected.
  - Every token text/background pair meets WCAG AA (4.5:1) in both themes. A link
    colour token was added for dark mode, where the brand blue measured 3.5:1.

**Decisions**
- Fixed an overflow bug: visually-hidden captions inside scrolling tables widened the
  page on phones. Scroll containers are now the captions' positioning context.
- No server-side rules changed.

## Phase 6: Automated timetable generation (OR-Tools CP-SAT)

**Built**
- `generator/` app with `GenerationRun` (parameters, status, solver status, objective,
  duration, reasons, metrics, user) and `ProposedLesson` (the complete candidate
  timetable, with kept lessons marked). Both are read-only in the admin.
- `generator/model.py` reads configuration through `day_schedule` and the models:
  - Variables:
    - `x[r,t,d,p]` exists only for valid slots where the class and teacher are free and
      the teacher has capacity left that day.
    - A designated teacher is the only candidate.
    - For other requirements, `y[r,t]` with `Σy = 1` picks one teacher for the whole
      week (`x ⇒ y`).
  - Hard constraints: exactly the periods still needed per requirement (or at most, in
    best-effort mode), one lesson per class slot, one lesson per teacher slot,
    capacity per teacher-day, and kept lessons fixed.
  - Soft constraints (weights in `settings.GENERATOR["WEIGHTS"]`):
    - the per-subject daily limit;
    - spreading each subject across the week;
    - no more than 3 consecutive lessons for a teacher (recess breaks a run);
    - no gaps in a teacher's day;
    - balanced utilisation among teachers who share a subject.
- `generator/diagnose.py`:
  - Six counting checks, each a necessary condition, so any reason it gives is definite:
    - nobody can teach a requirement;
    - a class needs more lessons than it has free slots;
    - a class needs more lessons than its teachers can cover on the days they work;
    - a designated teacher's load exceeds their capacity;
    - a designated teacher and their class share too few slots;
    - a subject's demand exceeds the capacity of the teachers who can take it.
  - Fallback: a best-effort solve that names the requirements left short.
- `generator/services.py`:
  - `run_generation()`:
    - refuses on data-health errors and on kept lessons that break the current rules;
    - in complete mode, runs the counting checks before solving;
    - solves, explains any failure, and stores the proposal with quality metrics.
  - `apply_run()`:
    - re-validates EVERY proposed lesson with `scheduling.validators` against the current
      configuration, treating the whole proposal as if it were live;
    - checks the live timetable has not changed since the run;
    - then, in one transaction, clears unlocked lessons (keep-locked mode), calls
      `scheduling.services.assign_lesson` for each new lesson, and writes a
      `generator_apply` audit row.
    - On any failure nothing changes.
- `manage.py generate_timetable [--keep all|locked] [--best-effort] [--time-limit N] [--seed N] [--no-apply]`.
- Generator page (`/generator/`):
  - parameters, health summary, recent runs;
  - synchronous run with a progress message, and the submit button disabled meanwhile.
- Run review (`/generator/runs/<id>/`):
  - outcome and reasons;
  - diff counts against the live timetable (new, changed, removed, kept, unchanged);
  - quality metrics for the proposal against the current timetable;
  - the proposal grid per class or per teacher with diff colours;
  - *Apply* for editors.
- `requirements.txt` now lists `ortools`.

**Decisions**
- `generate_timetable` keeps all existing lessons by default and applies the result
  when one is found. This satisfies "`seed_demo` then `generate_timetable` produces a
  complete timetable" without ever deleting manual work unless `--keep locked` is given.
  The Generator page defaults to the same non-destructive mode.
- Best-effort mode also refuses on data-health errors, as the spec requires a refusal
  before solving.
- For an undesignated requirement that already has kept lessons, the kept teacher is the
  only candidate, preserving "one teacher per class and subject for the week".
- Soft-quality verdicts on the review page are shown only when the current timetable is
  at least 90 % as complete as the proposal. Otherwise they are not comparable.
- Run permissions: running needs `generator.add_generationrun`; applying needs
  `generator.change_generationrun` and `scheduling.change_timetableentry`. The
  Timetable Editor group receives these automatically (post-migrate sync).

**Measured**: on the demo school (10 classes, 116 requirements, 486 lessons), the
hard-constraint model solves optimally in about 0.4 s. With soft constraints it returns
a complete timetable within the time limit (FEASIBLE; quality improves with time).
Applying 486 lessons through the service takes about 5 s on SQLite.

**Try it**: `python manage.py seed_demo && python manage.py generate_timetable`, or open
*Generator*, run, review, apply.
