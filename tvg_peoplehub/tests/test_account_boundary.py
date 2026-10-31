"""Real HTTP login checks for the staff-only PeopleHub URL tree."""

import re

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings

from horilla.settings.peoplehub import MIDDLEWARE, TEMPLATES
from horilla.testkit import make_company, make_employee, make_user

HOST = "people.thevipgroup.eu"
PASSWORD = "Synthetic-Initial-Password-83!"
NEW_PASSWORD = "Synthetic-Replacement-Password-94!"


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    LOGIN_URL="peoplehub:login",
    ALLOWED_HOSTS=[HOST, "testserver"],
    TWO_FACTORS_AUTHENTICATION=False,
    MIDDLEWARE=MIDDLEWARE,
    TEMPLATES=TEMPLATES,
)
class StaffAccountBoundaryTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Login Boundary Employer")
        self.user = make_user("synthetic.portal.login", password=PASSWORD)
        self.employee = make_employee(
            company=self.company, user=self.user, email="portal-login@example.invalid"
        )
        self.client = Client(HTTP_HOST=HOST)

    def login(self, client=None, **extra):
        return (client or self.client).post(
            "/login/", {"username": self.user.username, "password": PASSWORD, **extra}
        )

    def test_no_signup_or_legacy_account_creation_route_creates_users(self):
        before = get_user_model().objects.count()
        for path in (
            "/signup/",
            "/sign-up/",
            "/register/",
            "/create-account/",
            "/candidate/create-account/",
            "/accounts/signup/",
            "/api/auth/register/",
            "/initialize-database/",
            "/onboarding/user-creation/synthetic-token/",
        ):
            for method in ("get", "post"):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path)
                    self.assertEqual(response.status_code, 404)
        self.assertEqual(get_user_model().objects.count(), before)
        page = self.client.get("/login/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'href="/candidate/create-account/')
        self.assertNotContains(page, 'href="/signup/')

    def test_employee_login_rotates_session_and_reaches_dashboard(self):
        session = self.client.session
        session["synthetic_prelogin"] = True
        session.save()
        before = session.session_key
        response = self.login()
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
        self.assertNotEqual(self.client.session.session_key, before)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_non_employee_and_disabled_accounts_cannot_log_in(self):
        for kind in ("no_employee", "disabled_user", "disabled_employee"):
            with self.subTest(kind=kind):
                user = make_user(f"synthetic.denied.{kind}", password=PASSWORD)
                if kind != "no_employee":
                    employee = make_employee(
                        company=self.company, user=user, email=f"{kind}@example.invalid"
                    )
                    if kind == "disabled_employee":
                        employee.is_active = False
                        employee.save(update_fields=("is_active",))
                    else:
                        user.is_active = False
                        user.save(update_fields=("is_active",))
                client = Client(HTTP_HOST=HOST)
                response = client.post(
                    "/login/", {"username": user.username, "password": PASSWORD}
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(SESSION_KEY, client.session)

    def test_candidate_cookie_is_not_an_employee_session(self):
        self.client.cookies["tvg_careerhub_sessionid"] = "synthetic-unrelated-session"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_login_rejects_external_redirect_and_wrong_host(self):
        response = self.login(next="https://career.thevipgroup.eu/candidate/profile/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(
            self.client.get("/login/", HTTP_HOST="career.thevipgroup.eu").status_code,
            400,
        )

    def test_login_and_logout_require_csrf_and_logout_is_post_only(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST=HOST)
        self.assertEqual(self.login(client).status_code, 403)
        page = client.get("/login/")
        csrf = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()
        ).group(1)
        self.assertEqual(self.login(client, csrfmiddlewaretoken=csrf).status_code, 302)
        self.assertEqual(client.get("/logout/").status_code, 405)
        self.assertEqual(client.post("/logout/").status_code, 403)
        self.assertEqual(
            client.post(
                "/logout/",
                {
                    "csrfmiddlewaretoken": client.cookies[
                        settings.CSRF_COOKIE_NAME
                    ].value
                },
            ).status_code,
            302,
        )
        self.assertNotIn(SESSION_KEY, client.session)

    def test_new_employee_must_change_password_then_can_use_dashboard(self):
        self.user.is_new_employee = True
        self.user.save(update_fields=("is_new_employee",))
        self.login()
        self.assertRedirects(
            self.client.get("/"), "/change-password/", fetch_redirect_response=False
        )
        self.assertEqual(self.client.get("/change-password/").status_code, 200)
        invalid = self.client.post(
            "/change-password/",
            {
                "old_password": "synthetic-wrong",
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_new_employee)
        self.assertTrue(self.user.check_password(PASSWORD))
        other_session = Client(HTTP_HOST=HOST)
        self.login(other_session)
        response = self.client.post(
            "/change-password/",
            {
                "old_password": PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_new_employee)
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(other_session.get("/").status_code, 302)
        self.assertNotIn(SESSION_KEY, other_session.session)

    def test_password_change_requires_authenticated_employee_and_csrf(self):
        self.assertEqual(self.client.get("/change-password/").status_code, 302)
        client = Client(enforce_csrf_checks=True, HTTP_HOST=HOST)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                "/change-password/",
                {
                    "old_password": PASSWORD,
                    "new_password1": NEW_PASSWORD,
                    "new_password2": NEW_PASSWORD,
                },
            ).status_code,
            403,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_password_change_never_targets_a_supplied_user_id(self):
        other = make_user("synthetic.unchanged.peer", password=PASSWORD)
        other_company = make_company("Synthetic Other Employer", hq=False)
        make_employee(company=other_company, user=other, email="peer@example.invalid")
        self.login()
        response = self.client.post(
            "/change-password/",
            {
                "user_id": other.pk,
                "username": other.username,
                "old_password": PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertTrue(other.check_password(PASSWORD))

    def test_password_change_rechecks_active_employee_link(self):
        for kind in ("no_employee", "inactive_employee"):
            with self.subTest(kind=kind):
                user = make_user(f"synthetic.password.{kind}", password=PASSWORD)
                if kind == "inactive_employee":
                    employee = make_employee(
                        company=self.company,
                        user=user,
                        email="inactive-password@example.invalid",
                    )
                    employee.is_active = False
                    employee.save(update_fields=("is_active",))
                client = Client(HTTP_HOST=HOST)
                for method in ("get", "post"):
                    client.force_login(user)
                    response = getattr(client, method)(
                        "/change-password/",
                        (
                            {
                                "old_password": PASSWORD,
                                "new_password1": NEW_PASSWORD,
                                "new_password2": NEW_PASSWORD,
                            }
                            if method == "post"
                            else {}
                        ),
                    )
                    if kind == "no_employee":
                        # Native CompanyMiddleware logs out a user whose Employee
                        # link is missing, before the view checks authorization.
                        self.assertRedirects(
                            response,
                            "/login/?next=/change-password/",
                            fetch_redirect_response=False,
                        )
                        self.assertNotIn(SESSION_KEY, client.session)
                    else:
                        self.assertEqual(response.status_code, 403)
                user.refresh_from_db()
                self.assertTrue(user.check_password(PASSWORD))

    def test_invalid_new_password_does_not_clear_first_login_gate(self):
        self.user.is_new_employee = True
        self.user.save(update_fields=("is_new_employee",))
        self.login()
        for first, second in (("password", "password"), (NEW_PASSWORD, "mismatch")):
            with self.subTest(reason="weak" if first == second else "mismatch"):
                response = self.client.post(
                    "/change-password/",
                    {
                        "old_password": PASSWORD,
                        "new_password1": first,
                        "new_password2": second,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.user.refresh_from_db()
                self.assertTrue(self.user.is_new_employee)
                self.assertTrue(self.user.check_password(PASSWORD))
