# School Timetable Management & Automation System

Django application for managing school structure, teacher capacity, subject
requirements and class timetables, with an automatic generator planned for
a later phase.

**Status: Phase 1 (project foundation).** Models, migrations, Django Admin
and tests are in place. The timetable builder arrives in Phase 3.

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then edit SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/admin/ and sign in.

`migrate` seeds the school calendar: Monday–Saturday, 9 periods a day,
recess after period 4. Change it under **School settings** and **School days**.

## Tests

```bash
python manage.py test
```

## Project layout

| Path | Purpose |
|---|---|
| `config/` | Settings (env-driven), URLs, WSGI/ASGI |
| `school/` | Configuration: calendar, grades, sections, classes, subjects, teachers, capacities, requirements |
| `school/day_schedule.py` | The only source of "which days, how many periods, where recess goes" |
| `scheduling/` | Timetable entries; the scheduling service layer arrives in Phase 3 |
| `templates/` | Project templates (currently the shared admin action form) |

## Key rules

- **Daily capacity, not free periods.** `TeacherDailyAvailability.max_periods`
  is the most lessons a teacher can teach that day, in any periods. A teacher
  with no record for a day cannot be scheduled that day.
- **Clashes are blocked by the database.** `TimetableEntry` is unique on
  `(class, day, period)` and on `(teacher, day, period)` across the whole school.
- **One write path.** Timetable entries are read-only in the admin. From
  Phase 3 every change goes through `scheduling/services.py`.
- **Day layouts are configurable.** Each day can override the default
  number of periods and recess position. Shortening a day is refused while
  lessons or teacher capacities would fall outside it.

## Working with Git branches

SQLite data is not versioned by Git. To keep branches isolated, point each
branch at its own database file in `.env`:

```
SQLITE_PATH=db_feature_generator.sqlite3
```

then run `python manage.py migrate` on that branch.
