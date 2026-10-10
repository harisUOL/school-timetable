import importlib
import sys

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings


class SecretKeyTests(SimpleTestCase):
    def import_fresh(self, module):
        sys.modules.pop(module, None)
        return importlib.import_module(module)

    @override_settings(SECRET_KEY_IS_EPHEMERAL=True)
    def test_servers_refuse_a_throwaway_secret_key(self):
        for module in ("config.wsgi", "config.asgi"):
            with self.subTest(module=module), self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
                self.import_fresh(module)

    @override_settings(SECRET_KEY_IS_EPHEMERAL=False)
    def test_servers_start_with_a_configured_key(self):
        for module in ("config.wsgi", "config.asgi"):
            with self.subTest(module=module):
                self.assertTrue(callable(self.import_fresh(module).application))
