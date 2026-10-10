from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from school.constants import Day
from school.models import TeacherDailyAvailability
from school.roles import EDITOR_GROUP, VIEWER_GROUP

from .factories import make_class, make_requirement, make_subject, make_teacher, make_user

PAGES = ["scheduling:dashboard", "school:availability", "school:health", "school:settings"]


class RoleGroupTests(TestCase):
    def test_groups_created_by_migration(self):
        editor = Group.objects.get(name=EDITOR_GROUP)
        viewer = Group.objects.get(name=VIEWER_GROUP)
        editor_perms = set(editor.permissions.values_list("codename", flat=True))
        viewer_perms = set(viewer.permissions.values_list("codename", flat=True))
        self.assertIn("change_timetableentry", editor_perms)
        self.assertIn("change_teacherdailyavailability", editor_perms)
        self.assertIn("view_teacher", viewer_perms)
        self.assertTrue(all(p.startswith("view_") for p in viewer_perms))


class PageAccessTests(TestCase):
    def setUp(self):
        class_group = make_class()
        maths = make_subject()
        teacher = make_teacher(subjects=[maths], capacity=5)
        make_requirement(class_group, maths, periods=4, teacher=teacher)
        make_requirement(class_group, make_subject("Physics"), periods=3)  # a health error

    def test_anonymous_users_are_redirected_to_login(self):
        for name in PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, f"{reverse('login')}?next={reverse(name)}")

    def test_every_page_renders_for_each_role(self):
        users = [
            make_user("admin", superuser=True),
            make_user("editor", EDITOR_GROUP),
            make_user("viewer", VIEWER_GROUP),
        ]
        for user in users:
            self.client.force_login(user)
            for name in PAGES:
                with self.subTest(user=user.username, page=name):
                    response = self.client.get(reverse(name))
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, "Sign out")

    def test_user_without_a_role_is_forbidden(self):
        self.client.force_login(make_user("nobody"))
        self.assertEqual(self.client.get(reverse("scheduling:dashboard")).status_code, 403)

    def test_login_page_and_logout(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        make_user("viewer", VIEWER_GROUP)
        response = self.client.post(reverse("login"), {"username": "viewer", "password": "pw"})
        self.assertRedirects(response, reverse("scheduling:dashboard"))
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

    def test_sidebar_marks_current_page_and_disables_missing_ones(self):
        from unittest import mock

        from config import navigation

        self.client.force_login(make_user("admin", superuser=True))
        response = self.client.get(reverse("scheduling:dashboard"))
        self.assertContains(response, 'aria-current="page"')
        self.assertNotContains(response, 'aria-disabled="true"')  # every page exists now
        with mock.patch.object(navigation, "SIDEBAR", [*navigation.SIDEBAR, ("Future", "nope:missing")]):
            response = self.client.get(reverse("scheduling:dashboard"))
        self.assertContains(response, 'aria-disabled="true"')

    def test_health_page_lists_issues_with_admin_links(self):
        self.client.force_login(make_user("admin", superuser=True))
        response = self.client.get(reverse("school:health"))
        self.assertContains(response, "no active teacher is qualified")
        self.assertContains(response, reverse("admin:school_teacher_changelist"))

    def test_dashboard_shows_counts_and_issues(self):
        self.client.force_login(make_user("viewer", VIEWER_GROUP))
        response = self.client.get(reverse("scheduling:dashboard"))
        self.assertContains(response, "0 of 7 required lessons placed")
        self.assertContains(response, "no active teacher is qualified")


class AvailabilityGridTests(TestCase):
    url = reverse("school:availability")

    def setUp(self):
        self.teacher = make_teacher("Ms A", capacity={Day.MONDAY: 4, Day.TUESDAY: 5})
        self.other = make_teacher("Mr B")
        self.client.force_login(make_user("editor", EDITOR_GROUP))

    def grid_data(self, **overrides):
        data = {}
        for teacher in (self.teacher, self.other):
            for day in range(Day.MONDAY, Day.SUNDAY):
                record = TeacherDailyAvailability.objects.filter(teacher=teacher, day=day).first()
                data[f"cap-{teacher.pk}-{day}"] = str(record.max_periods) if record else ""
        data.update(overrides)
        return data

    def capacities(self, teacher):
        return dict(teacher.daily_availability.values_list("day", "max_periods"))

    def test_shows_grid_and_weekly_totals(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'name="cap-{self.teacher.pk}-{Day.MONDAY}"')
        self.assertContains(response, '<td class="num strong">9</td>', html=True)
        self.assertContains(response, "Save capacities")

    def test_saves_creates_updates_and_deletes(self):
        response = self.client.post(
            self.url,
            self.grid_data(
                **{
                    f"cap-{self.teacher.pk}-{Day.MONDAY}": "6",  # update
                    f"cap-{self.teacher.pk}-{Day.TUESDAY}": "",  # delete → unavailable
                    f"cap-{self.other.pk}-{Day.FRIDAY}": "3",  # create
                }
            ),
        )
        self.assertRedirects(response, self.url)
        self.assertEqual(self.capacities(self.teacher), {Day.MONDAY: 6})
        self.assertEqual(self.capacities(self.other), {Day.FRIDAY: 3})

    def test_rejects_invalid_input_and_saves_nothing(self):
        response = self.client.post(
            self.url,
            self.grid_data(
                **{
                    f"cap-{self.teacher.pk}-{Day.MONDAY}": "10",  # Monday has 9 periods
                    f"cap-{self.teacher.pk}-{Day.TUESDAY}": "abc",
                    f"cap-{self.other.pk}-{Day.FRIDAY}": "3",  # valid, but not saved
                }
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Monday has only 9 teaching periods", status_code=400)
        self.assertContains(response, "Enter a whole number", status_code=400)
        self.assertContains(response, 'aria-invalid="true"', count=2, status_code=400)
        self.assertEqual(self.capacities(self.teacher), {Day.MONDAY: 4, Day.TUESDAY: 5})
        self.assertEqual(self.capacities(self.other), {})

    def test_rejects_negative_numbers(self):
        response = self.client.post(self.url, self.grid_data(**{f"cap-{self.other.pk}-{Day.MONDAY}": "-1"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.capacities(self.other), {})

    def test_viewer_cannot_save(self):
        self.client.force_login(make_user("viewer", VIEWER_GROUP))
        response = self.client.get(self.url)
        self.assertNotContains(response, "Save capacities")
        response = self.client.post(self.url, self.grid_data(**{f"cap-{self.other.pk}-{Day.MONDAY}": "3"}))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.capacities(self.other), {})

    def test_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(make_user("editor2", EDITOR_GROUP))
        response = client.post(self.url, self.grid_data())
        self.assertEqual(response.status_code, 403)


class LayoutTests(TestCase):
    def test_base_layout_accessibility_hooks(self):
        self.client.force_login(make_user("viewer", VIEWER_GROUP))
        response = self.client.get(reverse("scheduling:dashboard"))
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-live="assertive"')
        self.assertContains(response, 'id="theme-toggle"')
        self.assertContains(response, 'aria-controls="sidebar-nav"')
        self.assertContains(response, 'href="#main"')
        self.assertContains(response, "js/app.js")

    def test_messages_render_as_toasts(self):
        self.client.force_login(make_user("editor", EDITOR_GROUP))
        make_teacher("Ms A", capacity={Day.MONDAY: 4})
        response = self.client.post(reverse("school:availability"), {}, follow=True)
        self.assertContains(response, 'class="toast toast-success"')
