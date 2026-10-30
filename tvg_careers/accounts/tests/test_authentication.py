from __future__ import annotations

import re
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tvg_careers.accounts.models import CandidateAccount, CandidateSecurityEvent
from tvg_careers.accounts.services import (
    IdentityReviewRequired,
    IdentityServiceUnavailable,
)
from tvg_careers.accounts.tests.factories import TEST_PASSWORD, make_candidate_account
from tvg_careers.accounts.tokens import make_email_verification_token

SIGNUP_DATA = {
    "first_name": "Synthetic",
    "last_name": "Applicant",
    "email": "applicant@example.test",
    "password1": TEST_PASSWORD,
    "password2": TEST_PASSWORD,
    "privacy_notice_acknowledged": "on",
}

LOW_LIMITS = {
    "signup_ip": (10, 900),
    "signup_email": (3, 3600),
    "verification_ip": (20, 3600),
    "login_ip": (30, 900),
    "login_pair": (1, 900),
    "recovery_ip": (10, 3600),
    "recovery_email": (3, 3600),
}


class CandidateRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_registration_hashes_password_records_notice_and_sends_verification(self):
        response = self.client.post(reverse("careerhub:candidate-signup"), SIGNUP_DATA)

        self.assertEqual(response.status_code, 200)
        account = CandidateAccount.objects.get()
        self.assertEqual(account.email, "applicant@example.test")
        self.assertFalse(account.is_active)
        self.assertIsNone(account.email_verified_at)
        self.assertTrue(account.check_password(TEST_PASSWORD))
        self.assertNotEqual(account.password, TEST_PASSWORD)
        self.assertEqual(
            account.privacy_notice_version,
            settings.CAREERHUB_PRIVACY_NOTICE_VERSION,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(TEST_PASSWORD, mail.outbox[0].body)
        self.assertContains(response, "does not confirm whether an account")
        self.assertTrue(
            CandidateSecurityEvent.objects.filter(
                account=account,
                event_type=CandidateSecurityEvent.EventType.ACCOUNT_CREATED,
            ).exists()
        )

    def test_duplicate_registration_is_generic_and_does_not_replace_credentials(self):
        original = make_candidate_account(
            email="applicant@example.test",
            verified=False,
            first_name="Original",
        )
        original_hash = original.password
        response = self.client.post(
            reverse("careerhub:candidate-signup"),
            {**SIGNUP_DATA, "first_name": "Attacker"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CandidateAccount.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.first_name, "Original")
        self.assertEqual(original.password, original_hash)
        self.assertEqual(len(mail.outbox), 1)

    def test_verified_duplicate_is_generic_and_does_not_send_account_details(self):
        make_candidate_account(email="applicant@example.test")
        response = self.client.post(reverse("careerhub:candidate-signup"), SIGNUP_DATA)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotContains(response, "applicant@example.test")
        self.assertContains(response, "does not confirm whether an account")

    def test_notice_acknowledgement_and_strong_password_are_required(self):
        response = self.client.post(
            reverse("careerhub:candidate-signup"),
            {
                **SIGNUP_DATA,
                "password1": "password",
                "password2": "password",
                "privacy_notice_acknowledged": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(CandidateAccount.objects.count(), 0)
        self.assertContains(response, "privacy notice", status_code=400)

    def test_registration_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            client.post(reverse("careerhub:candidate-signup"), SIGNUP_DATA).status_code,
            403,
        )

    @override_settings(
        CAREERHUB_AUTH_RATE_LIMITS={
            **LOW_LIMITS,
            "signup_ip": (1, 900),
        }
    )
    def test_registration_is_rate_limited_by_network(self):
        first = self.client.post(reverse("careerhub:candidate-signup"), SIGNUP_DATA)
        second = self.client.post(
            reverse("careerhub:candidate-signup"),
            {**SIGNUP_DATA, "email": "second-applicant@example.test"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(CandidateAccount.objects.count(), 1)


class CandidateVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.account = make_candidate_account(verified=False)
        self.url = reverse(
            "careerhub:candidate-verify",
            kwargs={"token": make_email_verification_token(self.account)},
        )

    @patch("tvg_careers.accounts.views.PeopleOpsIdentityClient.provision")
    def test_verification_requires_confirmation_then_links_person(self, provision):
        provision.return_value = {
            "person_public_id": str(self.account.person_public_id),
            "status": "created",
        }
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)

        response = self.client.post(self.url)

        self.assertRedirects(
            response,
            f"{reverse('careerhub:candidate-login')}?verified=1",
            fetch_redirect_response=False,
        )
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertIsNotNone(self.account.email_verified_at)
        self.assertEqual(
            self.account.identity_status, CandidateAccount.IdentityStatus.LINKED
        )

    @patch("tvg_careers.accounts.views.PeopleOpsIdentityClient.provision")
    def test_existing_different_person_requires_review_without_auto_merge(
        self, provision
    ):
        provision.side_effect = IdentityReviewRequired
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertEqual(
            self.account.identity_status,
            CandidateAccount.IdentityStatus.REVIEW_REQUIRED,
        )
        self.assertFalse(self.account.applicant_functions_enabled)

    @patch("tvg_careers.accounts.views.PeopleOpsIdentityClient.provision")
    def test_identity_outage_does_not_partially_activate_account(self, provision):
        provision.side_effect = IdentityServiceUnavailable
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 503)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertIsNone(self.account.email_verified_at)

    def test_invalid_verification_token_is_rejected(self):
        response = self.client.post(
            reverse("careerhub:candidate-verify", kwargs={"token": "invalid"})
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(CAREERHUB_EMAIL_VERIFICATION_TIMEOUT=60)
    def test_expired_verification_token_is_rejected(self):
        with patch("django.core.signing.time.time", return_value=1_000):
            token = make_email_verification_token(self.account)
        with patch("django.core.signing.time.time", return_value=1_061):
            response = self.client.post(
                reverse("careerhub:candidate-verify", kwargs={"token": token})
            )

        self.assertEqual(response.status_code, 400)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)

    def test_verification_confirmation_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)


class CandidateLoginAndProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.account = make_candidate_account()

    def test_verified_account_login_rotates_session_and_unverified_is_denied(self):
        session = self.client.session
        session["pre_login"] = "synthetic"
        session.save()
        old_session_key = session.session_key

        response = self.client.post(
            reverse("careerhub:candidate-login"),
            {"email": self.account.email, "password": TEST_PASSWORD},
        )

        self.assertRedirects(
            response,
            reverse("careerhub:candidate-profile"),
            fetch_redirect_response=False,
        )
        self.assertNotEqual(self.client.session.session_key, old_session_key)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.account.pk))

        self.client.post(reverse("careerhub:candidate-logout"))
        unverified = make_candidate_account(
            email="unverified@example.test", verified=False
        )
        denied = self.client.post(
            reverse("careerhub:candidate-login"),
            {"email": unverified.email, "password": TEST_PASSWORD},
        )
        self.assertEqual(denied.status_code, 400)
        self.assertNotIn(SESSION_KEY, self.client.session)

    @override_settings(CAREERHUB_AUTH_RATE_LIMITS=LOW_LIMITS)
    def test_login_is_generic_and_rate_limited_by_email_ip_pair(self):
        first = self.client.post(
            reverse("careerhub:candidate-login"),
            {"email": self.account.email, "password": "wrong-password"},
        )
        second = self.client.post(
            reverse("careerhub:candidate-login"),
            {"email": self.account.email, "password": "wrong-password"},
        )

        self.assertEqual(first.status_code, 400)
        self.assertContains(first, "email or password is incorrect", status_code=400)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)

    def test_external_next_redirect_is_rejected(self):
        response = self.client.post(
            f"{reverse('careerhub:candidate-login')}?next=https://attacker.example/",
            {"email": self.account.email, "password": TEST_PASSWORD},
        )
        self.assertRedirects(
            response,
            reverse("careerhub:candidate-profile"),
            fetch_redirect_response=False,
        )

    def test_profile_requires_login_and_never_updates_another_account(self):
        other = make_candidate_account(email="other@example.test")
        login_ok = self.client.login(
            email=self.account.email,
            password=TEST_PASSWORD,
        )
        self.assertTrue(login_ok)
        response = self.client.post(
            reverse("careerhub:candidate-profile"),
            {
                "first_name": "Updated",
                "last_name": "Candidate",
                "talent_pool_consent": "on",
                "email": other.email,
                "person_public_id": str(other.person_public_id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.account.first_name, "Updated")
        self.assertTrue(self.account.talent_pool_consent)
        self.assertEqual(other.first_name, "Synthetic")
        profile = self.client.get(reverse("careerhub:candidate-profile"))
        self.assertNotContains(profile, other.email)

        anonymous = Client().get(reverse("careerhub:candidate-profile"))
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("careerhub:candidate-login"), anonymous.url)

    def test_logout_is_post_only_and_invalidates_session(self):
        self.client.login(email=self.account.email, password=TEST_PASSWORD)
        self.assertEqual(
            self.client.get(reverse("careerhub:candidate-logout")).status_code,
            405,
        )
        response = self.client.post(reverse("careerhub:candidate-logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_logout_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(email=self.account.email, password=TEST_PASSWORD))

        response = client.post(reverse("careerhub:candidate-logout"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            client.get(reverse("careerhub:candidate-profile")).status_code,
            200,
        )

    def test_public_candidate_can_never_receive_staff_permissions(self):
        self.assertFalse(self.account.is_staff)
        self.assertFalse(self.account.is_superuser)
        self.assertFalse(self.account.has_perm("recruitment.view_candidate"))
        self.assertFalse(self.account.has_module_perms("recruitment"))


class CandidateRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.account = make_candidate_account()

    def test_recovery_response_is_generic_and_email_contains_one_time_link(self):
        unknown = self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": "unknown@example.test"},
        )
        known = self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": self.account.email},
        )

        self.assertEqual(unknown.status_code, 302)
        self.assertEqual(known.status_code, 302)
        self.assertEqual(unknown.url, known.url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(TEST_PASSWORD, mail.outbox[0].body)
        self.assertIn("/candidate/password/reset/confirm/", mail.outbox[0].body)

    def test_password_reset_changes_password_and_invalidates_existing_session(self):
        existing_session = Client()
        self.assertTrue(
            existing_session.login(
                email=self.account.email,
                password=TEST_PASSWORD,
            )
        )
        self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": self.account.email},
        )
        match = re.search(
            r"http://testserver(/candidate/password/reset/confirm/[^\s]+)",
            mail.outbox[0].body,
        )
        self.assertIsNotNone(match)
        token_response = self.client.get(match.group(1))
        self.assertEqual(token_response.status_code, 302)
        reset_url = token_response.url
        new_password = "Replacement-Synthetic-Password-82!"
        completed = self.client.post(
            reset_url,
            {"new_password1": new_password, "new_password2": new_password},
        )

        self.assertRedirects(
            completed,
            reverse("careerhub:candidate-password-reset-complete"),
            fetch_redirect_response=False,
        )
        self.account.refresh_from_db()
        self.assertTrue(self.account.check_password(new_password))
        self.assertFalse(self.account.check_password(TEST_PASSWORD))
        self.assertEqual(
            existing_session.get(reverse("careerhub:candidate-profile")).status_code,
            302,
        )
        replay = self.client.get(match.group(1), follow=True)
        self.assertContains(replay, "invalid or expired")

    def test_unverified_account_does_not_receive_recovery_email(self):
        unverified = make_candidate_account(
            email="unverified@example.test", verified=False
        )
        response = self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": unverified.email},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        CAREERHUB_AUTH_RATE_LIMITS={
            **LOW_LIMITS,
            "recovery_ip": (1, 900),
        }
    )
    def test_recovery_is_rate_limited_by_network(self):
        first = self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": "first-unknown@example.test"},
        )
        second = self.client.post(
            reverse("careerhub:candidate-password-reset"),
            {"email": "second-unknown@example.test"},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)
