import json
from uuid import uuid4

from django.test import Client, TestCase

from horilla.testkit import make_company
from tvg_core.models import Person

IDENTITY_TOKEN = "synthetic-identity-command-token-000000000000"


class CareerHubIdentityAPITests(TestCase):
    def setUp(self):
        self.company = make_company("THEVIPGROUP Identity Employer")
        self.client = Client()
        self.person_public_id = uuid4()
        self.payload = {
            "account_id": str(uuid4()),
            "person_public_id": str(self.person_public_id),
            "email": "synthetic.identity@example.test",
            "preferred_name": "Synthetic Identity",
        }

    def _settings(self, **overrides):
        values = {
            "ROOT_URLCONF": "tvg_careers.peopleops_urls",
            "CAREERHUB_IDENTITY_API_ENABLED": True,
            "CAREERHUB_IDENTITY_API_TOKEN": IDENTITY_TOKEN,
            "TVG_LEGAL_EMPLOYER_ID": self.company.pk,
        }
        values.update(overrides)
        return self.settings(**values)

    def _post(self, payload=None, token=IDENTITY_TOKEN):
        return self.client.post(
            "/internal/careerhub/v1/identities/",
            data=json.dumps(payload or self.payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_identity_command_requires_its_exact_token(self):
        with self._settings():
            missing = self.client.post(
                "/internal/careerhub/v1/identities/",
                data=json.dumps(self.payload),
                content_type="application/json",
            )
            wrong = self._post(token="wrong-token")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(Person.all_objects.count(), 0)

    def test_identity_command_creates_person_idempotently_for_legal_employer(self):
        with self._settings():
            created = self._post()
            repeated = self._post()

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["status"], "created")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["status"], "linked")
        self.assertEqual(Person.all_objects.count(), 1)
        person = Person.all_objects.get()
        self.assertEqual(person.public_id, self.person_public_id)
        self.assertEqual(person.legal_employer, self.company)
        self.assertEqual(person.primary_email, self.payload["email"])
        self.assertIn("private, no-store", created.headers["Cache-Control"])

    def test_matching_email_with_different_person_requires_review(self):
        existing = Person.all_objects.create(
            legal_employer=self.company,
            primary_email=self.payload["email"],
            preferred_name="Existing Synthetic Person",
        )
        with self._settings():
            response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Person.all_objects.count(), 1)
        self.assertNotEqual(existing.public_id, self.person_public_id)
        self.assertNotIn(self.payload["email"], response.content.decode())

    def test_disabled_or_misconfigured_identity_boundary_fails_closed(self):
        with self._settings(CAREERHUB_IDENTITY_API_ENABLED=False):
            disabled = self._post()
        with self._settings(TVG_LEGAL_EMPLOYER_ID=None):
            no_employer = self._post()

        self.assertEqual(disabled.status_code, 404)
        self.assertEqual(no_employer.status_code, 503)
        self.assertEqual(Person.all_objects.count(), 0)

    def test_invalid_payload_is_rejected_without_echo(self):
        with self._settings():
            response = self._post({**self.payload, "person_public_id": "not-a-uuid"})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.payload["email"], response.content.decode())

    def test_peopleops_masks_legacy_candidate_credentials_routes(self):
        with self._settings():
            login = self.client.get("/recruitment/candidate-login/")
            tracking = self.client.get("/recruitment/candidate-self-status-tracking/")

        self.assertEqual(login.status_code, 404)
        self.assertEqual(tracking.status_code, 404)
