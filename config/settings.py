"""
Django settings for the School Timetable project.

Configuration comes from environment variables. A local `.env` file is read
if present (see `.env.example`), so no extra dependency is needed.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no overriding."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

DEBUG = env_bool("DEBUG", default=False)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
# True when no SECRET_KEY was configured and a throwaway one is in use.
# Management commands (migrate, test, seed_demo…) still work on a fresh clone,
# but config/wsgi.py and config/asgi.py refuse to serve requests with it.
SECRET_KEY_IS_EPHEMERAL = False
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        from django.core.management.utils import get_random_secret_key

        SECRET_KEY = get_random_secret_key()
        SECRET_KEY_IS_EPHEMERAL = True

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Railway (and similar hosts) publish the app's public domain: trust it automatically.
PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if PUBLIC_DOMAIN:
    ALLOWED_HOSTS.append(PUBLIC_DOMAIN)
    CSRF_TRUSTED_ORIGINS.append(f"https://{PUBLIC_DOMAIN}")
if os.environ.get("RAILWAY_ENVIRONMENT"):
    ALLOWED_HOSTS.append("healthcheck.railway.app")  # Railway's health checker

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps
    "school.apps.SchoolConfig",
    "scheduling.apps.SchedulingConfig",
    "generator.apps.GeneratorConfig",
    "exports.apps.ExportsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files in production (no separate web server needed).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.navigation.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# SQLite by default. Set SQLITE_PATH per Git branch to keep branch data
# separate (e.g. SQLITE_PATH=db_feature_x.sqlite3).
# Set DATABASE_URL (postgres://user:password@host:port/name, as Railway
# provides it) or POSTGRES_DB plus the other POSTGRES_* vars to use PostgreSQL.


def _database_from_url(url: str) -> dict:
    from urllib.parse import unquote, urlparse

    parts = urlparse(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"DATABASE_URL must be a postgres:// URL, not {parts.scheme}://")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "localhost",
        "PORT": str(parts.port or 5432),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(os.environ["DATABASE_URL"])}
elif os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", ""),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "ATOMIC_REQUESTS": False,
        }
    }
else:
    sqlite_path = Path(os.environ.get("SQLITE_PATH", "db.sqlite3"))
    if not sqlite_path.is_absolute():
        sqlite_path = BASE_DIR / sqlite_path
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            # Wait for a competing write lock instead of failing immediately.
            "OPTIONS": {"timeout": 20},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "scheduling:dashboard"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Compressed copies of CSS/JS, served by WhiteNoise.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# --------------------------------------------------------------------------
# Security hardening outside development
# --------------------------------------------------------------------------

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Behind an HTTPS proxy (Railway): trust its header so Django knows requests are secure.
if env_bool("BEHIND_HTTPS_PROXY", default=bool(PUBLIC_DOMAIN)):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = not DEBUG
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # the health check arrives over plain HTTP

# --------------------------------------------------------------------------
# Timetable rules and generator weights
# --------------------------------------------------------------------------

# Preferred most lessons of one subject per class per day. A soft rule: the
# generator penalises going over it and Data Health warns when a designated
# teacher's days make it unavoidable.
TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY = 2

# Automatic generator (generator/model.py). Weights are relative: a higher
# weight makes the solver work harder to avoid that pattern. Hard rules
# (clashes, capacities, requirements) are never traded against these.
GENERATOR = {
    "DEFAULT_TIME_LIMIT": int(os.environ.get("GENERATOR_TIME_LIMIT", 60)),  # seconds
    "MAX_TIME_LIMIT": 600,
    "WORKERS": int(os.environ.get("GENERATOR_WORKERS", 8)),
    "WEIGHTS": {
        # Best-effort mode: each requirement lesson left unplaced.
        "unplaced_lesson": 1000,
        # Each lesson of a subject beyond TIMETABLE_MAX_SUBJECT_LESSONS_PER_DAY in one class-day.
        "subject_daily_excess": 40,
        # Reward for each distinct day a class has a subject (spreads it across the week).
        "subject_spread_day": 6,
        # Each run of more than 3 consecutive lessons for a teacher (recess breaks a run).
        "teacher_consecutive": 8,
        # Each free period between a teacher's first and last lesson of a day.
        "teacher_gap": 3,
        # Spread (max − min, in per-mille of weekly capacity) of utilisation among
        # teachers who share a subject.
        "teacher_balance": 1,
    },
    # Maximum consecutive lessons before the consecutive penalty applies.
    "MAX_CONSECUTIVE": 3,
}
