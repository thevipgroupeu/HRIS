"""Synthetic, CSRF-enforced checks on the intended public hostname."""

import re
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from tvg_careers.accounts.models import CandidateAccount
from tvg_careers.accounts.tests.factories import TEST_PASSWORD, make_candidate_account

HOST = "career.thevipgroup.eu"


@override_settings(
    ALLOWED_HOSTS=[HOST, "testserver"],
    CAREERHUB_PUBLIC_BASE_URL=f"https://{HOST}",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CandidateLoginBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(
            enforce_csrf_checks=True,
            HTTP_HOST=HOST,
            HTTP_ORIGIN=f"https://{HOST}",
        )

    def csrf(self, response):
        match = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()
        )
        self.assertIsNotNone(match)
        return match.group(1)

    @patch("tvg_careers.accounts.views.PeopleOpsIdentityClient.provision")
    def test_signup_email_confirmation_login_and_logout_with_real_csrf(self, provision):
        # No real identity API call or email. Exercise the views in one session.
        provision.return_value = {"status": "created"}
        page = self.client.get("/candidate/create-account/", secure=True)
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            "/candidate/create-account/",
            {
                "first_name": "Synthetic",
                "last_name": "Portal Check",
                "email": "synthetic-portal-check@example.invalid",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "privacy_notice_acknowledged": "on",
                "csrfmiddlewaretoken": self.csrf(page),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        account = CandidateAccount.objects.get()
        self.assertFalse(account.is_active)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https://[^\s]+/candidate/verify/[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(link)
        parsed = urlsplit(link.group(0))
        self.assertEqual(parsed.netloc, HOST)
        confirmation = self.client.get(parsed.path, secure=True)
        self.assertEqual(confirmation.status_code, 200)
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        response = self.client.post(
            parsed.path,
            {
                "csrfmiddlewaretoken": self.csrf(confirmation),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        provision.assert_called_once()
        account.refresh_from_db()
        self.assertTrue(account.applicant_functions_enabled)

        login = self.client.get("/candidate/sign-in/", secure=True)
        response = self.client.post(
            "/candidate/sign-in/",
            {
                "email": account.email,
                "password": TEST_PASSWORD,
                "csrfmiddlewaretoken": self.csrf(login),
            },
            secure=True,
        )
        self.assertRedirects(
            response, "/candidate/profile/", fetch_redirect_response=False
        )
        self.assertEqual(self.client.session[SESSION_KEY], str(account.pk))
        profile = self.client.get("/candidate/profile/", secure=True)
        self.assertEqual(profile.status_code, 200)
        self.assertNotIn("tvg_peoplehub_sessionid", self.client.cookies)
        self.assertFalse(self.client.cookies[settings.SESSION_COOKIE_NAME]["domain"])
        response = self.client.post(
            "/candidate/sign-out/",
            {
                "csrfmiddlewaretoken": self.csrf(profile),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_candidate_authentication_never_exposes_staff_routes(self):
        account = make_candidate_account(email="synthetic-scope@example.invalid")
        self.client.force_login(account)
        for path in (
            "/admin/",
            "/employee/",
            "/recruitment/",
            "/login/",
            "/change-password/",
            "/api/auth/register/",
            "/initialize-database/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, secure=True).status_code, 404)
        self.assertFalse(account.has_perm("employee.view_employee"))

    def test_staff_cookie_and_host_cannot_authenticate_a_candidate(self):
        self.client.cookies["tvg_peoplehub_sessionid"] = "synthetic-staff-cookie"
        response = self.client.get("/candidate/profile/", secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(
            self.client.get(
                "/candidate/sign-in/", HTTP_HOST="people.thevipgroup.eu", secure=True
            ).status_code,
            400,
        )
