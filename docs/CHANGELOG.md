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

## Phase 7: Export and administration

**Built**
- `exports/` app. Every export uses the same selectors as the screens (`exports/tables.py`).
  - Print views:
    - one class (`/exports/print/class/<id>/`);
    - one teacher (`/exports/print/teacher/<id>/`);
    - *Print all classes* (`/exports/print/classes/`).
    - `static/css/print.css` gives A4 landscape, one timetable per page, and no app
      chrome. The toolbar's Print button is handled by `static/js/print.js`.
  - Excel (`/exports/excel/`, openpyxl): one sheet per class, a *Teachers* sheet (every
    teacher's week), and a *Workload* sheet (assigned/max per day, week, max, remaining).
  - PDF (reportlab, A4 landscape):
    - per class (`/exports/pdf/class/<id>/`);
    - per teacher (`/exports/pdf/teacher/<id>/`);
    - whole school (`/exports/pdf/school/`): every class, every teacher, then the
      workload table.
  - Export buttons: on the class and teacher pages (Print, PDF) and on the dashboard
    (Print all classes, Whole-school PDF, Excel workbook).
- Roles enforced across every view:
  - `config/tests/test_roles.py` checks anonymous, no-role, Viewer, Editor and
    Administrator against every project URL.
  - It fails if a new URL is added without being listed.
  - The sidebar hides pages the user cannot open.
- README rewritten: setup, commands, features, roles, how the generator works, and every
  generator setting.

**Decisions**
- The Settings page is now **administrators only**, matching "Superuser: admin and
  settings". It used to be read-only for every role.
- Viewers and Editors can export. Every export requires login and
  `scheduling.view_timetableentry`.
- Fresh clone without `.env`:
  - Previously the settings refused to load without `SECRET_KEY` (DEBUG off), so the
    required `pip install -r requirements.txt && python manage.py migrate && python
    manage.py test` failed on a fresh clone.
  - Management commands now fall back to a random throwaway key.
  - `config/wsgi.py` and `config/asgi.py` (and therefore `runserver`) still refuse to
    serve requests until `SECRET_KEY` is set or `DEBUG=True`, so production remains
    fail-closed.
- `requirements.txt` adds `openpyxl` and `reportlab`, both specified for Phase 7 in
  CLAUDE.md.

**Try it**: on the dashboard, use *Print all classes*, *Whole-school PDF* or *Excel workbook*.

## Import: teacher allotment workbook

**Built**
- `manage.py import_allotment <file.xlsx> [--dry-run] [--report skipped.csv]` reads the
  "Allotment" sheet (Teacher / Class / Subject / Periods per week) and creates or updates:
  - grades (Sr Nur, Prep, I–X), classes and subjects;
  - teachers and their qualifications;
  - each class's requirements, with the allotted teacher as the designated teacher.
- It sets the week to Monday–Friday 9 periods and Saturday 6.
- New teachers get capacity equal to the day's length on every operating day. Capacities
  that already exist are never overwritten.
- Re-running with an updated sheet brings each imported class's requirements in line
  with it.
- Class labels now read "VII A" / "Sr Nur A" when the grade name ends in a letter.
  Numeric grades stay "10A".

**Decisions** (agreed with the school)
- Allocations the model cannot represent yet are skipped and listed, so their periods stay
  free in each class's week. Rule 4 of CLAUDE.md is unchanged.
  - Combined lessons: one row for several classes, such as AF6-10 across VI–X, TKW pairs,
    a PHE pair and HIN/URD for X A + X C.
  - Parallel groups: several teachers taking one class and subject at once, such as ScL
    labs, HIN/URD groups, A&M, CL, HC, and X History/Civics.
- Subject codes with a clear meaning get full names. Unknown codes (TKW, CL, A&M, ACT,
  AF1-5, AF6-10) keep the code as their name and can be renamed in the admin.

## School week editor and bell times

**Built**
- Bell times: the school has a start time, a period length and a recess length (defaults
  08:00, 40 and 20 minutes). Any day may override them, as it already could for periods
  and recess position.
  - `DaySchedule.timings` works out each period's start and end. `school/day_schedule.py`
    is still the only source.
  - The day must finish by midnight, which the models and the form both check.
- The Settings page (administrators only) now edits the school week in one form:
  - the defaults;
  - per-day open/closed, periods, recess position, start time, period length and recess
    length. A blank field inherits the default.
  - A *Bell times* table lists every day's hours and each period's times.
  - The whole week is validated against the new values and saved in one transaction, or
    not at all. Lessons beyond a shortened day always block the save.
  - *Keep teachers' daily capacities in step* (ticked by default): a full-day capacity
    follows the day's new length, up or down. Any other capacity above the new length is
    lowered to it. Refined capacities below the length are kept. Unticked, shortening a
    day is refused while capacities exceed it, as before.
- Every timetable shows the times:
  - class, teacher and proposal grids, print views, Excel and PDF;
  - a column heading shows the times when every day agrees;
  - a day with different times shows them in its own cells.
- The admin edits the new fields too.

**Changed**
- `import_allotment` no longer sets the school week. The week is configured on the
  Settings page, so re-importing a sheet never undoes it.
- The school's week is now Monday–Thursday 9 periods, Friday 6 and Saturday 9 (applied
  through the Settings form, which moved 180 full-day capacities with it).

## Timing groups and clock-time clashes

**Built**
- **Timing groups** (`TimingGroup`, `TimingGroupDay`, `Grade.timing_group`): grades that keep
  their own periods, recess and bell times on some or all days. A blank field inherits the
  school week, and whether the school is open is still school-wide.
- `school.day_schedule.Calendar` resolves the school week and every group into one
  `DaySchedule` per class and day. Validation shared by the models, the admin and the
  Settings page lives in `calendar_errors()` and `fit_capacities()`.
- **Teacher clashes by clock time.** Groups ring different bells, so a teacher may not teach
  two lessons whose times overlap, whatever their period numbers.
  - Validators: `teacher_overlap()` replaces `teacher_slot_lesson()`. The message names the
    other lesson's time, e.g. "… is already teaching 10A on Monday, period 3 (08:50–09:30)."
  - Generator: for each teacher and day, at most one possible lesson may cover any lesson's
    start time, which forbids every overlap exactly. Consecutive-run and gap penalties and
    the matching quality metrics are counted on the teacher's *home* schedule for the day
    (`generator/timeline.py`).
  - The database constraints on (teacher, day, period) for live entries and proposals were
    removed (migrations scheduling 0003 and generator 0002). They would wrongly forbid
    lessons at the same period number but different times. No data is changed. The
    services still validate under the teacher's row lock.
- **Per-class layouts everywhere:**
  - class grids, cells and slot options (which now include the period's time);
  - requirement caps (`periods_per_week` up to the class's own week);
  - data health and conflicts.
  - Capacities are checked against the longest day any class has.
- **Teacher timetables:** a teacher whose classes share one bell schedule gets the usual
  grid. A teacher across groups gets each day's lessons in time order, each with its time
  and the class's period numeral. The same applies to proposals, print, Excel and PDF.
- **Settings:**
  - bell times per group;
  - a *Timing groups* list with add, edit and delete pages (name, grades, per-day layout,
    keep capacities in step);
  - groups can also be edited in the admin.
  - Deleting a group is refused if any lesson would fall outside its class's day.

**Changed**
- CLAUDE.md: a teacher never teaches overlapping lessons, and rule 5 covers timing groups
  and clock-time clashes. Agreed with the school.
- The school's 2026-27 timings:

  | Group | Mon–Thu, Sat | Friday |
  |---|---|---|
  | Nursery, Sr. Nursery | 09:00–12:00: 4 × 40 min, 20-min recess after period 2 | 09:00–11:00: 3 × 40 min, no recess |
  | Prep | 09:00–13:10: 6 × 40 min, 10-min recess after period 3 | 09:00–11:00: 3 × 40 min, no recess |
  | Class I–V | 07:30–13:10: 8 × 40 min, 20-min recess after period 4 | 07:30–12:00: 6 × 40 min, 30-min recess after period 3 |
  | Class VI–X | 07:30–13:45: 9 × 40 min, 15-min recess after period 4 | 07:00–12:00: 6 × 45 min, 30-min recess after period 3 |

  The school week defaults match Class VI–X. A trial generation on the real data placed
  all 1769 periods with no overlapping teacher lessons.
