# Deploying to Railway

The app runs on Railway as one web service plus a PostgreSQL database. The repository is
ready for it:

| File | Does |
|---|---|
| `railway.json` | Before each deploy: `migrate`. Start: `collectstatic`, then gunicorn (2 workers × 4 threads, 900 s timeout so a long generator run is not cut off). Health check: `/healthz/`. |
| `.python-version` | Python 3.12 |
| `requirements.txt` | Adds `psycopg[binary]` (PostgreSQL), `gunicorn` (WSGI server), `whitenoise` (static files) |
| `config/settings.py` | Reads `DATABASE_URL`, and trusts `RAILWAY_PUBLIC_DOMAIN` for allowed hosts, CSRF and HTTPS |

## 1. Put the code on GitHub (private repository)

```bash
git remote add origin git@github.com:<you>/school-timetable.git
git push -u origin master
```

Never commit `db.sqlite3*`, `.env`, `deploy-data/` or `docs/allotment-skipped.csv`. They
are git-ignored because they hold staff names.

## 2. Create the Railway project

1. **New Project → Deploy from GitHub repo →** the repository.
2. **+ New → Database → PostgreSQL** in the same project.
3. Set these variables on the web service:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (a reference to the database service) |
   | `SECRET_KEY` | a long random string (50+ characters), never shared |
   | `DEBUG` | `False` |
   | `TIME_ZONE` | `Asia/Kolkata` (or the school's zone) |
   | `GENERATOR_WORKERS` | the service's vCPU count, e.g. `4` |
   | `GENERATOR_TIME_LIMIT` | `180` (the generator page's default time limit) |

4. **Settings → Networking → Generate Domain.** `RAILWAY_PUBLIC_DOMAIN` is then set
   automatically, and the app trusts it.
5. Deploy. Migrations run before the app starts; `/healthz/` must answer `ok`.

## 3. Copy the school's data

The export holds everything except sessions and generator runs:

```bash
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -e sessions -e admin.logentry -e generator \
  -o deploy-data/school-data.json
```

Load it once, from this machine, into Railway's database. Use the Postgres service's
**public** URL (`DATABASE_PUBLIC_URL` on its Variables tab):

```bash
DATABASE_URL='postgresql://…@…proxy.rlwy.net:PORT/railway' python manage.py migrate
DATABASE_URL='postgresql://…@…proxy.rlwy.net:PORT/railway' python manage.py loaddata deploy-data/school-data.json
```

Your existing logins (usernames and passwords) come across too. To add an administrator
later, run `railway run python manage.py createsuperuser`, or the same `DATABASE_URL=…`
trick with `createsuperuser`.

## Notes

- Back up Postgres from Railway's database **Backups** tab before big changes, such as a
  new school year.
- Optional hardening once HTTPS works: set `SECURE_HSTS_SECONDS` in settings.
