"""
Role enforcement across EVERY project view.

- Anonymous users are redirected to the login page.
- Signed-in users with no role get 403.
- Viewers can read everything except editor-only and administrator-only pages,
  and every write is refused with 403.
- Timetable Editors can read and write, but not the administrator-only pages.
- Superusers can do everything.

`test_every_project_url_is_covered` fails if a new URL is added without
being listed here, so no view can slip through untested.
"""

import json

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from generator.models import GenerationRun
from school.models import TimingGroup
from school.roles import EDITOR_GROUP, VIEWER_GROUP
from school.tests.factories import make_class, make_requirement, make_subject, make_teacher, make_user

APPS = ("school", "scheduling", "generator", "exports")
ALL, EDITOR, ADMIN = "all", "editor", "admin"  # who may GET the page

# name -> (args factory, who may read)
READ_PAGES = {
    "scheduling:dashboard": (lambda o: [], ALL),
    "scheduling:timetable": (lambda o: [], ALL),
    "scheduling:class_timetable": (lambda o: [o.c.pk], ALL),
    "scheduling:reset_class": (lambda o: [o.c.pk], EDITOR),
    "scheduling:teachers": (lambda o: [], ALL),
    "scheduling:teacher_timetable": (lambda o: [o.t.pk], ALL),
    "scheduling:workloads": (lambda o: [], ALL),
    "scheduling:subjects": (lambda o: [], ALL),
    "scheduling:requirements": (lambda o: [], ALL),
    "scheduling:conflicts": (lambda o: [], ALL),
    "scheduling:slot_options": (lambda o: [], ALL),
    "school:availability": (lambda o: [], ALL),
    "school:health": (lambda o: [], ALL),
    "school:settings": (lambda o: [], ADMIN),
    "school:timing_group_new": (lambda o: [], ADMIN),
    "school:timing_group_edit": (lambda o: [o.group.pk], ADMIN),
    "generator:index": (lambda o: [], ALL),
    "generator:run_detail": (lambda o: [o.gen_run.pk], ALL),
    "exports:print_classes": (lambda o: [], ALL),
    "exports:print_class": (lambda o: [o.c.pk], ALL),
    "exports:print_teacher": (lambda o: [o.t.pk], ALL),
    "exports:excel": (lambda o: [], ALL),
    "exports:pdf_class": (lambda o: [o.c.pk], ALL),
    "exports:pdf_teacher": (lambda o: [o.t.pk], ALL),
    "exports:pdf_school": (lambda o: [], ALL),
}
QUERY = {"scheduling:slot_options": lambda o: {"class": o.c.pk, "day": 0, "period": 1}}

# name -> (args factory, body factory, JSON?)
WRITES = {
    "scheduling:assign_lesson": (lambda o: [], lambda o: {"class_id": o.c.pk, "day": 0, "period": 1, "subject_id": o.s.pk, "teacher_id": o.t.pk}, True),
    "scheduling:clear_lesson": (lambda o: [], lambda o: {"class_id": o.c.pk, "day": 0, "period": 1}, True),
    "scheduling:lock_lesson": (lambda o: [], lambda o: {"class_id": o.c.pk, "day": 0, "period": 1, "locked": True}, True),
    "scheduling:reset_class": (lambda o: [o.c.pk], lambda o: {}, False),
    "school:availability": (lambda o: [], lambda o: {}, False),
    "scheduling:add_activity": (lambda o: [], lambda o: {"class_id": o.c.pk, "day": 0, "period": 2, "label": "Assembly", "length": 1}, True),
    "scheduling:remove_activity": (lambda o: [], lambda o: {"class_id": o.c.pk, "day": 0, "period": 2}, True),
    "generator:index": (lambda o: [], lambda o: {"keep_mode": "keep_all_existing", "mode": "complete", "time_limit": 1, "random_seed": 0}, False),
    "generator:apply_run": (lambda o: [o.gen_run.pk], lambda o: {}, False),
}

# Administrator-only writes: name -> (args factory, body factory)
ADMIN_WRITES = {
    "school:timing_group_delete": (lambda o: [o.group.pk], lambda o: {}),
}


def project_url_names():
    names = set()

    def walk(patterns, namespace):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, p.namespace or namespace)
            elif isinstance(p, URLPattern) and namespace in APPS and p.name:
                names.add(f"{namespace}:{p.name}")

    walk(get_resolver().url_patterns, None)
    return names


class RoleMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.c = make_class()
        cls.s = make_subject()
        cls.t = make_teacher(subjects=[cls.s], capacity=5)
        make_requirement(cls.c, cls.s, periods=3, teacher=cls.t)
        cls.group = TimingGroup.objects.create(name="Juniors")
        cls.gen_run = GenerationRun.objects.create(status=GenerationRun.Status.INFEASIBLE, message="test")
        cls.users = {
            "nobody": make_user("nobody"),
            "viewer": make_user("viewer", VIEWER_GROUP),
            "editor": make_user("editor", EDITOR_GROUP),
            "admin": make_user("admin", superuser=True),
        }

    def get(self, name):
        args_for, _ = READ_PAGES[name]
        query = QUERY.get(name, lambda o: {})(self)
        return self.client.get(reverse(name, args=args_for(self)), query)

    def post(self, name):
        args_for, body_for, is_json = WRITES[name]
        url = reverse(name, args=args_for(self))
        if is_json:
            return self.client.post(url, json.dumps(body_for(self)), content_type="application/json")
        return self.client.post(url, body_for(self))

    def test_every_project_url_is_covered(self):
        covered = set(READ_PAGES) | set(WRITES) | set(ADMIN_WRITES)
        self.assertEqual(project_url_names() - covered, set(), "Add new URLs to the role matrix")

    def test_anonymous_is_redirected_to_login(self):
        for name in READ_PAGES:
            with self.subTest(get=name):
                response = self.get(name)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))
        for name in WRITES:
            with self.subTest(post=name):
                self.assertEqual(self.post(name).status_code, 302)

    def test_user_without_role_is_forbidden_everywhere(self):
        self.client.force_login(self.users["nobody"])
        for name in READ_PAGES:
            with self.subTest(get=name):
                self.assertEqual(self.get(name).status_code, 403)
        for name in WRITES:
            with self.subTest(post=name):
                self.assertEqual(self.post(name).status_code, 403)

    def test_viewer_reads_but_never_writes(self):
        self.client.force_login(self.users["viewer"])
        for name, (_args, who) in READ_PAGES.items():
            with self.subTest(get=name):
                self.assertEqual(self.get(name).status_code, 200 if who == ALL else 403)
        for name in WRITES:
            with self.subTest(post=name):
                self.assertEqual(self.post(name).status_code, 403)

    def test_editor_reads_and_writes_but_not_admin_pages(self):
        self.client.force_login(self.users["editor"])
        for name, (_args, who) in READ_PAGES.items():
            with self.subTest(get=name):
                self.assertEqual(self.get(name).status_code, 403 if who == ADMIN else 200)
        for name in WRITES:
            with self.subTest(post=name):
                # Allowed through: 200, a redirect after success, or 400 for a rule violation.
                self.assertIn(self.post(name).status_code, (200, 302, 400))
        self.assertEqual(self.client.get("/admin/").status_code, 302)  # not staff

    def test_only_administrators_change_the_calendar(self):
        for role in ("nobody", "viewer", "editor"):
            self.client.force_login(self.users[role])
            for name, (args_for, body_for) in ADMIN_WRITES.items():
                with self.subTest(role=role, post=name):
                    response = self.client.post(reverse(name, args=args_for(self)), body_for(self))
                    self.assertEqual(response.status_code, 403)
        self.assertTrue(TimingGroup.objects.filter(pk=self.group.pk).exists())

    def test_superuser_can_open_everything(self):
        self.client.force_login(self.users["admin"])
        for name in READ_PAGES:
            with self.subTest(get=name):
                self.assertEqual(self.get(name).status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_sidebar_only_lists_permitted_pages(self):
        self.client.force_login(self.users["viewer"])
        response = self.client.get(reverse("scheduling:dashboard"))
        self.assertNotContains(response, reverse("school:settings"))
        self.client.force_login(self.users["admin"])
        response = self.client.get(reverse("scheduling:dashboard"))
        self.assertContains(response, reverse("school:settings"))
