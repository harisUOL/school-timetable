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
