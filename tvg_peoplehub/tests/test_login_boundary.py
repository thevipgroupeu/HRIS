from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from horilla.settings.peoplehub import MIDDLEWARE, TEMPLATES
from horilla.testkit import make_company, make_employee, make_user


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    ALLOWED_HOSTS=["people.thevipgroup.eu"],
    LOGIN_URL="peoplehub:login",
    LOGIN_REDIRECT_URL="peoplehub:dashboard",
    SESSION_COOKIE_NAME="tvg_peoplehub_sessionid",
    CSRF_COOKIE_NAME="tvg_peoplehub_csrftoken",
    MIDDLEWARE=MIDDLEWARE,
    TEMPLATES=TEMPLATES,
)
class PeopleHubLoginBoundaryTests(TestCase):
    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_container_healthcheck_uses_production_host_and_trusted_scheme(self):
        self.assertEqual(self.client.get("/health/").status_code, 301)
        response = self.client.get("/health/", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "peoplehub")

    def setUp(self):
        self.client = Client(HTTP_HOST="people.thevipgroup.eu")
        self.company = make_company("Synthetic Login Employer")
        self.user = make_user(
            "synthetic-staff-login", password="Synthetic-password-only-2610!"
        )
        self.employee = make_employee(
            company=self.company, user=self.user, email="staff@example.invalid"
        )
        self.user.is_new_employee = False
        self.user.save(update_fields=("is_new_employee",))
        self.credentials = {
            "username": self.user.username,
            "password": "Synthetic-password-only-2610!",
        }

    def test_signup_and_hr_admin_routes_are_absent_even_for_authenticated_employee(
        self,
    ):
        count = get_user_model().objects.count()
        for logged_in in (False, True):
            if logged_in:
                self.client.force_login(self.user)
            for path in (
                "/signup/",
                "/register/",
                "/candidate/create-account/",
                "/candidate/sign-in/",
                "/accounts/signup/",
                "/initialize-database/",
                "/admin/",
                "/employee/employee-create/",
                "/api/v1/auth/register/",
            ):
                with self.subTest(path=path, logged_in=logged_in):
                    self.assertEqual(self.client.get(path).status_code, 404)
                    self.assertEqual(
                        self.client.post(path, self.credentials).status_code, 404
                    )
        self.assertEqual(get_user_model().objects.count(), count)

    def test_login_dashboard_and_logout_with_real_csrf_checks(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="people.thevipgroup.eu")
        self.assertEqual(client.get("/login/").status_code, 200)
        self.assertEqual(client.post("/login/", self.credentials).status_code, 403)
        csrf = client.cookies["tvg_peoplehub_csrftoken"].value
        response = client.post(
            "/login/", {**self.credentials, "csrfmiddlewaretoken": csrf}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        self.assertEqual(client.get("/").status_code, 200)
        self.assertIn("tvg_peoplehub_sessionid", client.cookies)
        self.assertNotIn("tvg_careerhub_sessionid", client.cookies)
        self.assertEqual(client.get("/logout/").status_code, 405)
        csrf = client.cookies["tvg_peoplehub_csrftoken"].value
        self.assertEqual(
            client.post("/logout/", {"csrfmiddlewaretoken": csrf}).status_code, 302
        )
        self.assertEqual(client.get("/").status_code, 302)

    def test_disabled_employee_and_nonemployee_cannot_login(self):
        self.employee.is_active = False
        self.employee.save(update_fields=("is_active",))
        self.assertEqual(self.client.post("/login/", self.credentials).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        other = make_user(
            "synthetic-nonemployee", password=self.credentials["password"]
        )
        self.assertEqual(
            self.client.post(
                "/login/", {**self.credentials, "username": other.username}
            ).status_code,
            200,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_disabled_user_cannot_login_or_reuse_existing_session(self):
        self.assertEqual(self.client.post("/login/", self.credentials).status_code, 302)
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/"))
        self.client.logout()
        self.assertEqual(self.client.post("/login/", self.credentials).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_external_redirect_and_wrong_host_are_rejected(self):
        response = self.client.post(
            "/login/?next=https://career.thevipgroup.eu/", self.credentials
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        self.assertEqual(
            self.client.get("/login/", HTTP_HOST="career.thevipgroup.eu").status_code,
            400,
        )
