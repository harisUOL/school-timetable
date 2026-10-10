"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

from django.conf import settings  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

if settings.SECRET_KEY_IS_EPHEMERAL:
    raise ImproperlyConfigured(
        "SECRET_KEY is not set. Set it in the environment or .env (see .env.example) before "
        "serving requests, or set DEBUG=True for local development."
    )
