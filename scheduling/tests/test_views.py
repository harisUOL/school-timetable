import json

from django.test import Client
from django.urls import reverse

from school.constants import Day
from scheduling import services
from scheduling.models import TimetableAuditLog, TimetableEntry
from school.tests.factories import make_user

from .fixtures import SchoolFixture


class ApiTestCase(SchoolFixture):
    def setUp(self):
        self.client.force_login(self.editor)

    def post(self, name, payload, client=None):
        return (client or self.client).post(
            reverse(f"scheduling:{name}"), json.dumps(payload), content_type="application/json"
        )

    def payload(self, **overrides):
        data = {
            "class_id": self.c10a.pk,
            "day": Day.MONDAY,
            "period": 1,
            "subject_id": self.maths.pk,
            "teacher_id": self.ms_maths.pk,
        }
        data.update(overrides)
        return data


class PageTests(ApiTestCase):
    def test_index_renders_grade_and_class_selectors(self):
        response = self.client.get(reverse("scheduling:timetable"))
        self.assertContains(response, "Grade 10")
        self.assertContains(response, reverse("scheduling:class_timetable", args=[self.c10a.pk]))

    def test_index_redirects_to_chosen_class(self):
        response = self.client.get(reverse("scheduling:timetable") + f"?class={self.c10b.pk}")
        self.assertRedirects(response, reverse("scheduling:class_timetable", args=[self.c10b.pk]))

    def test_class_page_renders_grid_header_and_progress(self):
        self.assign(period=2)
        response = self.client.get(reverse("scheduling:class_timetable", args=[self.c10a.pk]))
        self.assertContains(response, "Class 10A")
        self.assertContains(response, '<span data-stat="filled">1</span> of 51 slots filled', html=False)
        self.assertContains(response, "<th scope=\"col\" class=\"period-col\">IV</th>", html=True)
        self.assertContains(response, 'data-slot="0-2"')
        self.assertContains(response, "Ms Maths")
        self.assertContains(response, "Reset timetable")
        self.assertContains(response, "js/timetable.js")
        self.assertContains(response, 'data-class-id="%d"' % self.c10a.pk)

    def test_over_capacity_warning(self):
        self.req_10a_physics.periods_per_week = 50
        self.req_10a_physics.save()
        response = self.client.get(reverse("scheduling:class_timetable", args=[self.c10a.pk]))
        self.assertContains(response, "requires 56 periods a week but has only 51")

    def test_viewer_sees_read_only_grid(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("scheduling:class_timetable", args=[self.c10a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<button type=\"button\" class=\"slot")
        self.assertNotContains(response, "Reset timetable")
        self.assertNotContains(response, "js/timetable.js")

    def test_unknown_class_is_404(self):
        self.assertEqual(self.client.get(reverse("scheduling:class_timetable", args=[999])).status_code, 404)

    def test_reset_confirmation_and_post(self):
        self.assign(period=1)
        self.assign(self.c10b, period=1, teacher=self.mr_physics)
        url = reverse("scheduling:reset_class", args=[self.c10a.pk])
        response = self.client.get(url)
        self.assertContains(response, "removes <strong>1 lesson</strong>", html=False)
        response = self.client.post(url, follow=True)
        self.assertRedirects(response, reverse("scheduling:class_timetable", args=[self.c10a.pk]))
        self.assertContains(response, "Removed 1 lesson(s) from 10A.")
        self.assertEqual(list(TimetableEntry.objects.values_list("class_group", flat=True)), [self.c10b.pk])
        self.assertTrue(TimetableAuditLog.objects.filter(action="reset").exists())

    def test_reset_empty_class_page(self):
        response = self.client.get(reverse("scheduling:reset_class", args=[self.c10b.pk]))
        self.assertContains(response, "has no lessons to remove")


class SlotOptionsApiTests(ApiTestCase):
    def test_returns_options(self):
        url = reverse("scheduling:slot_options")
        response = self.client.get(url, {"class": self.c10a.pk, "day": 0, "period": 1})
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["class"]["label"], "10A")
        self.assertEqual(data["period"]["roman"], "I")
        self.assertEqual([s["name"] for s in data["subjects"]], ["Mathematics", "Physics"])

    def test_error_paths(self):
        url = reverse("scheduling:slot_options")
        response = self.client.get(url, {"class": self.c10a.pk, "day": 5, "period": 7})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["violations"][0]["code"], "INVALID_SLOT")
        response = self.client.get(url, {"class": "x", "day": 0, "period": 1})
        self.assertEqual(response.json()["violations"][0]["code"], "BAD_REQUEST")
        self.assertEqual(self.client.get(url, {"class": 999, "day": 0, "period": 1}).status_code, 404)

    def test_viewer_can_read_options(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("scheduling:slot_options"), {"class": self.c10a.pk, "day": 0, "period": 1})
        self.assertEqual(response.status_code, 200)


class WriteApiTests(ApiTestCase):
    def test_assign_returns_cell_progress_stats_and_warnings(self):
        response = self.post("assign_lesson", self.payload(teacher_id=self.mr_physics.pk))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn('data-slot="0-1"', data["cell_html"])
        self.assertIn("Mr Physics", data["cell_html"])
        self.assertIn("1/6", data["progress_html"])
        self.assertEqual(data["stats"], {"filled": 1, "total_slots": 51, "required": 8, "assigned": 1})
        self.assertEqual([w["code"] for w in data["warnings"]], ["DESIGNATED_TEACHER_MISMATCH"])
        self.assertIn("Assigned Mathematics with Mr Physics", data["message"])

    def test_assign_uses_exactly_the_ids_sent(self):
        self.post("assign_lesson", self.payload(period=3, subject_id=self.physics.pk, teacher_id=self.mr_physics.pk))
        entry = TimetableEntry.objects.get()
        self.assertEqual((entry.subject, entry.teacher, entry.period), (self.physics, self.mr_physics, 3))

    def test_assign_violation_returns_400(self):
        self.assign(self.c10b, teacher=self.mr_physics)
        response = self.post("assign_lesson", self.payload(teacher_id=self.mr_physics.pk))
        self.assertEqual(response.status_code, 400)
        violation = response.json()["violations"][0]
        self.assertEqual(violation["code"], "TEACHER_CLASH")
        self.assertEqual(violation["context"]["class"], "10B")

    def test_replace_and_stale_replace(self):
        entry = self.assign().entry
        expected = services.entry_version(entry)
        response = self.post("assign_lesson", self.payload(teacher_id=self.mr_physics.pk, expected=expected))
        self.assertEqual(response.json()["action"], "replace")
        response = self.post("assign_lesson", self.payload(expected=expected))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["violations"][0]["code"], "STALE_SLOT")

    def test_clear(self):
        entry = self.assign().entry
        response = self.post("clear_lesson", self.payload(expected=services.entry_version(entry)))
        self.assertEqual(response.status_code, 200)
        self.assertIn("slot-empty", response.json()["cell_html"])
        self.assertFalse(TimetableEntry.objects.exists())
        response = self.post("clear_lesson", self.payload())
        self.assertEqual(response.json()["violations"][0]["code"], "SLOT_EMPTY")

    def test_lock(self):
        entry = self.assign().entry
        response = self.post("lock_lesson", self.payload(locked=True, expected=services.entry_version(entry)))
        self.assertEqual(response.status_code, 200)
        self.assertIn("is-locked", response.json()["cell_html"])
        self.assertTrue(TimetableEntry.objects.get().is_locked)
        response = self.post("lock_lesson", self.payload(locked="yes"))
        self.assertEqual(response.status_code, 400)
        response = self.post("lock_lesson", self.payload(period=5, locked=True))
        self.assertEqual(response.json()["violations"][0]["code"], "SLOT_EMPTY")

    def test_malformed_requests(self):
        url = reverse("scheduling:assign_lesson")
        response = self.client.post(url, "not json", content_type="application/json")
        self.assertEqual(response.json()["violations"][0]["code"], "BAD_REQUEST")
        for payload in (
            [1, 2],
            self.payload(class_id=999),
            self.payload(subject_id=None),
            self.payload(teacher_id=999),
            self.payload(day=True),
            self.payload(expected={"id": 1}),
        ):
            with self.subTest(payload=payload):
                response = self.post("assign_lesson", payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["violations"][0]["code"], "BAD_REQUEST")
        self.assertFalse(TimetableEntry.objects.exists())

    def test_get_is_not_allowed_on_write_endpoints(self):
        self.assertEqual(self.client.get(reverse("scheduling:assign_lesson")).status_code, 405)


class AccessTests(ApiTestCase):
    PAGES = [
        ("scheduling:timetable", []),
        ("scheduling:class_timetable", ["c10a"]),
        ("scheduling:reset_class", ["c10a"]),
    ]

    def url(self, name, args):
        return reverse(name, args=[getattr(self, a).pk for a in args])

    def test_anonymous_is_redirected_everywhere(self):
        self.client.logout()
        urls = [self.url(n, a) for n, a in self.PAGES] + [reverse("scheduling:slot_options") + "?class=1&day=0&period=1"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)
        for name in ("assign_lesson", "clear_lesson", "lock_lesson"):
            with self.subTest(name=name):
                response = self.post(name, self.payload())
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(TimetableEntry.objects.exists())

    def test_viewer_gets_403_on_every_write(self):
        self.client.force_login(self.viewer)
        entry = self.assign().entry
        expected = services.entry_version(entry)
        for name, payload in (
            ("assign_lesson", self.payload(period=2)),
            ("clear_lesson", self.payload(expected=expected)),
            ("lock_lesson", self.payload(locked=True, expected=expected)),
        ):
            with self.subTest(name=name):
                response = self.post(name, payload)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["violations"][0]["code"], "PERMISSION_DENIED")
        reset = self.url("scheduling:reset_class", ["c10a"])
        self.assertEqual(self.client.get(reset).status_code, 403)
        self.assertEqual(self.client.post(reset).status_code, 403)
        self.assertEqual(TimetableEntry.objects.count(), 1)
        self.assertFalse(TimetableEntry.objects.get().is_locked)

    def test_user_without_role_cannot_read(self):
        self.client.force_login(make_user("nobody"))
        self.assertEqual(self.client.get(self.url("scheduling:class_timetable", ["c10a"])).status_code, 403)

    def test_csrf_token_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        response = self.post("assign_lesson", self.payload(), client=client)
        self.assertEqual(response.status_code, 403)
        response = client.post(self.url("scheduling:reset_class", ["c10a"]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TimetableEntry.objects.exists())

    def test_csrf_header_is_accepted(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        page = client.get(self.url("scheduling:class_timetable", ["c10a"]))
        token = page.cookies["csrftoken"].value
        response = client.post(
            reverse("scheduling:assign_lesson"),
            json.dumps(self.payload()),
            content_type="application/json",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)
