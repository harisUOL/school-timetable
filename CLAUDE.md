# CLAUDE.md — School Timetable Management & Automation System

Project rules. Read this before every task in this repository.

## What this is

A Django app for a school with several grades and sections (9A, 9B, 10A …).
Administrators configure teachers, subjects, daily teaching capacities and
weekly subject requirements. They then build class timetables manually and,
later, generate a complete school-wide timetable automatically.

All class timetables share one pool of teachers. A teacher can never be in
two classes in the same slot, and their daily capacity counts lessons across
every class.

## Stack

- Python 3.12+, Django 5.2 LTS, Django ORM, Django Admin, Django auth.
- SQLite locally (`SQLITE_PATH` in `.env`), PostgreSQL optional (`POSTGRES_*`).
- Django templates, HTML5, CSS3, vanilla JavaScript, Fetch API.
- No frontend framework, no jQuery, no drag-and-drop.
- Phase 6: Google OR-Tools CP-SAT (`ortools`).
- Phase 7: `openpyxl` (Excel) and `reportlab` (PDF).
- Add no other dependency without stating why in the commit message.

## Commands

```bash
python manage.py test                          # must pass before every commit
python manage.py makemigrations --check --dry-run   # must report no changes
python manage.py check
python manage.py runserver
python manage.py seed_demo                     # from Phase 2
```

## Layout

| Path | Owns |
|---|---|
| `config/` | Settings (env-driven), URLs |
| `school/` | Configuration: calendar, grades, sections, classes, subjects, teachers, capacities, requirements |
| `school/day_schedule.py` | The ONLY source of operating days, periods per day and recess position |
| `scheduling/` | Timetable entries, service layer, builder views, workload, audit log |
| `scheduling/validators.py` | Pure rule checks that return violations. Used by services AND the generator |
| `scheduling/services.py` | The ONLY code that writes `TimetableEntry` rows |
| `scheduling/selectors.py` | Read-side queries: grids, workloads, progress |
| `generator/` | Phase 6: CP-SAT model, proposals |
| `exports/` | Phase 7: PDF, Excel, print views |
| `templates/`, `static/` | Shared layout, components, one JS module per page |

## Domain rules (do not change without asking)

1. **Daily capacity, not free periods.** `TeacherDailyAvailability.max_periods`
   is the most lessons a teacher may teach that day, in any periods.
   - Remaining capacity is `max_periods − count(that teacher's entries that day, across ALL classes)`.
   - Never count availability records as if they were capacity.
2. **A missing capacity record means 0.** The teacher cannot be scheduled that day.
3. **Designated teacher.** `SubjectRequirement.teacher` is optional.
   - When it is set, the manual builder pre-selects that teacher, and choosing someone else shows a warning but is allowed.
   - The generator treats a designated teacher as fixed.
   - When no teacher is designated, the generator picks ONE teacher for that class and subject for the whole week.
4. **No combined or split lessons (MVP).** There is one class, one teacher and one subject per slot.
   - Keep the service API shaped so that a `Lesson` that owns several classes or teachers can be added later.
5. **Day layouts come from `day_schedule`.** Never hardcode 6 days, 9 periods or recess after 4.
   - Recess is display-only. It is never a period and never counts towards workload.
6. **Subject allocation is capped.** A class may not receive more lessons of a subject than its `periods_per_week`.
   - The subject must have a requirement row for that class.
7. **Qualification is required.** The teacher must have a `TeacherSubject` row for the subject and must be active.

## Architecture rules

- **One write path.** Every create, replace, clear, reset and generator apply goes through `scheduling.services`.
  - Views, forms, admin, management commands and the generator never write `TimetableEntry` directly.
  - The admin for entries stays read-only.
- **Validation lives in `scheduling/validators.py`.** Views only parse input and render results. JavaScript validation is only for convenience; the server decides.
- **Writes use `transaction.atomic()`.**
  - Lock the affected teacher rows with `select_for_update()`, in ascending id order, before counting capacity.
  - Translate an `IntegrityError` from the uniqueness constraints into the same friendly clash message the validator would give.
- **Replacing an entry** validates with the current entry excluded, so its period is never counted twice. Use optimistic concurrency: the client sends the entry id and `updated_at` it saw.
- **Template data shape.** Selectors return grid data already shaped as rows of cells. Templates never need dict-lookup filters.
- **Every referenced template exists.** There is a test that renders each page and each error path.
- **Security.** Use CSRF on every state-changing request; never `csrf_exempt`. JS sends the `X-CSRFToken` header.
  - Every view requires login.
  - Writes require `scheduling.change_timetableentry`, which the "Timetable Editor" group has.
- **No hardcoded IDs in JS.** The client always sends the subject_id and teacher_id the user explicitly chose. Nothing defaults to "the first subject".
- **One JS event handler per action.** Use event delegation on the grid container.
- **One endpoint per operation.** Never two routes that both assign.

## Lessons from the previous attempt (do not repeat)

- Availability was modelled per period. The real rule is daily capacity.
- Capacity was computed by counting availability rows instead of using `max_periods`.
- The frontend hardcoded `subject_id = 1`, so every lesson became Biology.
- Duplicate endpoints and duplicate JS handlers fought each other.
- Capacity was counted per class instead of school-wide.
- Replacing an assignment double-counted workload.
- Templates needed custom dictionary filters.
- Missing templates surfaced only at runtime.
- Resetting a timetable touched availability configuration.
- Git branches shared one SQLite file. Use `SQLITE_PATH` per branch.

## Working conventions

- Read the existing code before changing it. Phase 1 already exists: models, admin, `day_schedule`, tests.
- Every behaviour change ships with tests in the same commit. Use Django's `TestCase`. Reuse `school/tests/factories.py`.
- Explain any destructive migration (dropped column, data rewrite) in the commit body before running it.
- Commit at each stable milestone with the message `Phase N: <summary>`.
- UK English in UI text. User-facing errors name the people and slots involved, e.g. "Teacher A is already teaching 10A on Monday, period 3."
