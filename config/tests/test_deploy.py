from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.settings import _database_from_url


class DatabaseUrlTests(SimpleTestCase):
    def test_parses_a_railway_url(self):
        db = _database_from_url("postgresql://postgres:p%40ss@db.internal:5433/railway")
        self.assertEqual(
            (db["ENGINE"], db["NAME"], db["USER"], db["PASSWORD"], db["HOST"], db["PORT"]),
            ("django.db.backends.postgresql", "railway", "postgres", "p@ss", "db.internal", "5433"),
        )

    def test_rejects_other_schemes(self):
        with self.assertRaises(ValueError):
            _database_from_url("mysql://x@y/z")


class HealthCheckTests(TestCase):
    def test_needs_no_login_and_shows_no_data(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual((response.status_code, response.content), (200, b"ok"))

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz/$"])
    def test_is_exempt_from_the_https_redirect(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
        self.assertEqual(self.client.get("/accounts/login/").status_code, 301)
