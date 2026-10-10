"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()

from django.conf import settings  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

if settings.SECRET_KEY_IS_EPHEMERAL:
    raise ImproperlyConfigured(
        "SECRET_KEY is not set. Set it in the environment or .env (see .env.example) before "
        "serving requests, or set DEBUG=True for local development."
    )
