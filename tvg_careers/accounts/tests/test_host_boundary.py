from django.test import TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["career.thevipgroup.eu"])
class CareerHubHostBoundaryTests(TestCase):
    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_container_healthcheck_uses_production_host_and_trusted_scheme(self):
        self.assertEqual(
            self.client.get("/health/", HTTP_HOST="career.thevipgroup.eu").status_code,
            301,
        )
        response = self.client.get(
            "/health/",
            HTTP_HOST="career.thevipgroup.eu",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_candidate_entry_pages_exist_on_external_hostname(self):
        for path in ("/candidate/create-account/", "/candidate/sign-in/"):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST="career.thevipgroup.eu")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "csrfmiddlewaretoken")

    def test_staff_login_signup_and_admin_are_not_public_routes(self):
        for path in (
            "/login/",
            "/admin/",
            "/employee/",
            "/initialize-database/",
            "/signup/",
            "/internal/careerhub/",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(
                        path, HTTP_HOST="career.thevipgroup.eu"
                    ).status_code,
                    404,
                )

    def test_internal_hostname_is_not_accepted_by_public_runtime(self):
        self.assertEqual(
            self.client.get(
                "/candidate/create-account/", HTTP_HOST="people.thevipgroup.eu"
            ).status_code,
            400,
        )
