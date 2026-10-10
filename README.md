# School Timetable Management & Automation System

A Django application for a school with several grades and sections (9A, 9B, 10A…).
Administrators configure teachers, subjects, daily teaching capacities and weekly
subject requirements. Editors then build class timetables by hand, generate a complete
whole-school timetable automatically with Google OR-Tools CP-SAT, or combine the two.

All class timetables share one pool of teachers. A teacher never teaches two lessons at
overlapping times, even when their classes ring different bells, and their daily capacity
counts lessons across every class.

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                   # set SECRET_KEY (and DEBUG=True for local work)
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo --with-users   # optional: a realistic demo school
python manage.py runserver
```

Sign in at http://127.0.0.1:8000/. The admin is at `/admin/`.

`migrate` seeds the school calendar (Monday–Saturday, 9 periods, recess after period 4,
08:00 start, 40-minute periods, 20-minute recess) and the **Timetable Editor** and
**Viewer** groups. Change the week and bell times on the **Settings** page.

Without a `.env`, management commands such as `migrate`, `test` and `seed_demo` still
run on a throwaway secret key. Serving requests (`runserver`, WSGI, ASGI) refuses to
start until `SECRET_KEY` is set or `DEBUG=True`.

## Commands

| Command | What it does |
|---|---|
| `python manage.py test` | Run the test suite (must pass before every commit) |
| `python manage.py makemigrations --check --dry-run` | Must report no changes |
| `python manage.py check` | Django system checks |
| `python manage.py seed_demo` | Create or refresh the demo school. Idempotent. |
| `python manage.py seed_demo --reset` | Remove the demo data, then seed it afresh |
| `python manage.py seed_demo --remove` | Remove only the demo data; nothing else is touched |
| `python manage.py import_allotment <file.xlsx>` | Import classes, subjects, teachers and requirements from an allotment workbook (`--dry-run`, `--report skipped.csv`). Leaves the school week alone. |
| `python manage.py seed_demo --with-users` | Also create `demo_editor` / `demo_viewer` (password `demo-timetable`) |
| `python manage.py generate_timetable` | Keep existing lessons, fill everything else, apply |
| `python manage.py generate_timetable --keep locked` | Rebuild everything except locked lessons |
| `python manage.py generate_timetable --best-effort` | Place as many lessons as possible when a complete timetable is impossible |
| `python manage.py generate_timetable --no-apply` | Store the proposal only; review and apply it on the Generator page |
| `python manage.py generate_timetable --time-limit 120 --seed 7` | Longer search; a different but equally valid timetable |

Quick start with demo data: `python manage.py seed_demo && python manage.py generate_timetable`
produces a complete, valid timetable (486 lessons for 10 classes).

## Features

- **Dashboard**:
  - counts of classes, teachers, subjects and requirements;
  - school-wide and per-class completion;
  - the most-loaded teachers;
  - conflict and data-health summaries;
  - links for exporting.
- **Class Timetables**:
  - Pick a grade, then a class, to see the weekly grid. Day lengths and recess positions
    come from the school calendar.
  - Click a slot to open the assignment dialog:
    - subjects with their progress;
    - qualified teachers with their status (available, or blocked by a clash with
      another class, a full day, or no capacity that day), their day and week loads,
      and the designated teacher pre-selected.
  - On an occupied slot you can change, remove, lock or unlock the lesson.
  - **Activities:** put something that is not a lesson (assembly, activity session) into
    one or more consecutive periods. It can be for one class, its grade, its timing group
    or the whole school. No lesson can go there, and the generator plans around it.
  - *Reset timetable* clears one class only, after confirmation. It snapshots the
    lessons to the audit log, and can keep locked lessons.
- **Teachers / Teacher Workloads**:
  - per-teacher weekly timetables across all classes;
  - a teachers × days grid of `assigned/max`;
  - colour states (free, near full, full, over capacity, no capacity), each with a text
    label;
  - filter by subject and sort by load.
- **Subjects**: demand across the school against the capacity of qualified teachers.
- **Subject Requirements**:
  - every class × subject with required, assigned and remaining lessons, the designated
    teacher, and a flag when someone else teaches it;
  - a **conflict report** of lessons that broke after the configuration changed (a
    teacher deactivated, a qualification removed, a capacity or requirement lowered).
    Each conflict links to its slots.
- **Availability**: a teachers × days grid of daily capacities, saved all or nothing.
  An empty cell means no capacity record, so the teacher is unavailable that day.
- **Settings** (administrators):
  - the school week: open days, periods per day and the recess position;
  - bell times: start time, period length and recess length, as defaults with per-day
    overrides;
  - **timing groups**: grades with their own hours (e.g. Nursery 09:00–12:00, Class VI–X
    07:30–13:45), each with per-day layouts;
  - a table of every group's period times.
  Timetables, prints and exports show these times. Teacher clashes are checked by clock
  time across groups. A teacher who teaches in several groups sees each day's lessons in
  time order.
- **Data Health**: configuration problems, as errors or warnings, each with a link to
  the admin page that fixes it.
- **Generator**: run CP-SAT, review the proposal, and apply it. See below.
- **Exports**:
  - print views (one timetable per page, plus *Print all classes*);
  - an Excel workbook (a sheet per class, plus Teachers and Workload sheets);
  - PDFs per class, per teacher, or for the whole school.
- **Interface**:
  - light and dark themes;
  - responsive down to phones, where the grid scrolls inside its own box;
  - keyboard-accessible dialog;
  - toasts announced through `aria-live`.

## Roles

| Role | Can do |
|---|---|
| **Viewer** (group) | Read every page and export. Cannot change anything. |
| **Timetable Editor** (group) | Everything a Viewer can, plus assign, change, remove, lock and reset lessons, edit daily capacities, and run and apply the generator. |
| **Administrator** (superuser) | Everything, plus the Settings page and the Django admin (calendar, classes, subjects, teachers, requirements, users). |

- Every page requires login.
- A signed-in user with no role sees 403.
- Writes need `scheduling.change_timetableentry`, held by the Timetable Editor group.
- Assign roles in the admin under *Users*.
- The groups' permissions are defined in `school/roles.py` and kept in sync after
  every `migrate`. The sync only ever adds permissions.

## How the generator works

1. **Pre-checks.** The Data Health checks run first, and any error refuses the run with
   the reasons. Lessons that would be kept must still satisfy every rule. In complete
   mode, quick counting checks look for a definite impossibility before solving, for
   example "10A needs 8 lessons but its teachers can cover only 4 of its free slots".
2. **Model** (`generator/model.py`):
   - Variables:
     - `x[requirement, teacher, day, period]` exists only where the slot is real, the
       class and teacher are free, and the teacher has capacity left that day.
     - A designated teacher is fixed.
     - Otherwise exactly one qualified active teacher is chosen per class and subject for
       the whole week.
   - Hard rules:
     - each requirement gets exactly its periods (or at most, in best-effort mode);
     - one lesson per class per slot;
     - a teacher's lessons never overlap in time, across the school (by clock time, since
       timing groups ring different bells);
     - lessons per teacher per day ≤ `max_periods`;
     - kept lessons stay where they are and use up capacity.
3. **Soft rules** are weighted, and never traded against hard rules.
4. **Result.** The run stores a *proposal* (`ProposedLesson`) and never touches the live
   timetable. If no timetable exists, the run explains why. The counting checks give
   definite reasons, and a best-effort solve names the requirements left short.
5. **Review.** The run page shows:
   - the solver status, time and objective;
   - diff counts against the live timetable;
   - quality metrics for the proposal next to the current timetable;
   - the proposal per class or per teacher, colour-coded as new, changed, removed or kept.
6. **Apply.** Every proposed lesson is re-validated with `scheduling/validators.py`
   against the current configuration, and the live timetable must not have changed since
   the run. Then, in one transaction:
   - unlocked lessons are cleared (keep-locked mode);
   - every new lesson goes through `scheduling.services.assign_lesson`;
   - an audit row is written.

   If anything fails, nothing changes.

**Parameters** (Generator page and command):

| Parameter | Meaning |
|---|---|
| Existing lessons | *Keep all*: existing lessons stay and only the gaps are filled (non-destructive, the default). *Keep locked only*: every unlocked lesson is replaced. |
| Goal | *Complete*: place every required lesson or explain why not. *Best effort*: place as many as possible. |
| Time limit | Seconds the solver may search (default 60). It returns the best timetable found so far. |
| Random seed | A different seed gives a different timetable of similar quality. |

**Settings** (`config/settings.py`):

| Setting | Meaning |
|---|---|
| `TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY` (2) | Preferred maximum lessons of one subject per class per day (soft). |
| `GENERATOR["DEFAULT_TIME_LIMIT"]` (60, env `GENERATOR_TIME_LIMIT`) | Default solver time limit in seconds. |
| `GENERATOR["MAX_TIME_LIMIT"]` (600) | Highest time limit the page accepts. |
| `GENERATOR["WORKERS"]` (8, env `GENERATOR_WORKERS`) | CP-SAT search threads. |
| `GENERATOR["MAX_CONSECUTIVE"]` (3) | Lessons in a row before the consecutive penalty applies (recess breaks a run). |
| `WEIGHTS["unplaced_lesson"]` (1000) | Best-effort mode: penalty per required lesson left unplaced. |
| `WEIGHTS["subject_daily_excess"]` (40) | Penalty per lesson of a subject beyond the daily limit in one class-day. |
| `WEIGHTS["subject_spread_day"]` (6) | Reward per distinct day a class has each subject (spreads subjects across the week). |
| `WEIGHTS["teacher_consecutive"]` (8) | Penalty per run longer than `MAX_CONSECUTIVE` for a teacher. |
| `WEIGHTS["teacher_gap"]` (3) | Penalty per free period between a teacher's first and last lesson of a day. |
| `WEIGHTS["teacher_balance"]` (1) | Penalty on the utilisation spread (max − min, per mille of weekly capacity) among teachers who share a subject. |

## Project layout

| Path | Owns |
|---|---|
| `config/` | Settings (env-driven), URLs, sidebar navigation |
| `school/` | Configuration: calendar, grades, sections, classes, subjects, teachers, capacities, requirements; data health; roles; availability grid; `seed_demo` |
| `school/day_schedule.py` | The only source of operating days, periods, recess and bell times: the school week and timing groups, resolved per class by `Calendar` |
| `scheduling/validators.py` | Pure rule checks returning violations. Used by the services, the dialog, the conflict report and the generator |
| `scheduling/services.py` | The only code that writes `TimetableEntry` rows (atomic, teacher row locks, optimistic concurrency, audit log) |
| `scheduling/selectors.py` | Read side: grids, progress, loads, slot options, workloads, conflicts |
| `generator/` | CP-SAT model, diagnosis, scoring, runs and proposals, `generate_timetable` |
| `exports/` | Print views, Excel (openpyxl) and PDF (reportlab) |
| `templates/`, `static/` | Shared layout, one stylesheet, one JS module per page |
| `docs/` | `PLAN.md` and `CHANGELOG.md` (what was built per phase, and decisions) |

The domain and architecture rules are in `CLAUDE.md`.

## Hosting

See `docs/DEPLOY.md`. It covers Railway: the web service plus PostgreSQL, the environment
variables, and copying the school's data across.

## Working with Git branches

SQLite data is not versioned by Git. Point each branch at its own database file in `.env`:

```
SQLITE_PATH=db_feature_generator.sqlite3
```

then run `python manage.py migrate` on that branch.
