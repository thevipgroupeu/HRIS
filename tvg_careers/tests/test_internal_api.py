from django.test import Client, TestCase

from horilla.testkit import make_company
from tvg_careers.tests.factories import make_job_opening
from tvg_core.tests.factories import make_venture

TOKEN = "synthetic-internal-careerhub-token-000000000000"


class CareerHubInternalAPITests(TestCase):
    def setUp(self):
        self.company = make_company("THEVIPGROUP API Employer")
        self.venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )
        self.opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Published API Role",
            is_published=True,
        )
        self.client = Client()

    def _settings(self):
        return self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            CAREERHUB_INTERNAL_API_ENABLED=True,
            CAREERHUB_INTERNAL_API_TOKEN=TOKEN,
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        )

    def test_api_requires_exact_bearer_token(self):
        with self._settings():
            missing = self.client.get("/internal/careerhub/v1/jobs/")
            wrong = self.client.get(
                "/internal/careerhub/v1/jobs/",
                HTTP_AUTHORIZATION="Bearer wrong-token",
            )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong.status_code, 404)

    def test_list_and_detail_return_allow_listed_public_projection(self):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
        with self._settings():
            listing = self.client.get(
                "/internal/careerhub/v1/jobs/?venture=fluxops&status=open",
                **headers,
            )
            detail = self.client.get(
                f"/internal/careerhub/v1/jobs/fluxops/{self.opening.public_slug}/",
                **headers,
            )

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.json()["count"], 1)
        self.assertEqual(detail.json()["slug"], self.opening.public_slug)
        serialized = str(detail.json()).lower()
        for private_name in (
            "recruitment_id",
            "hiring_managers",
            "candidate",
            "employee",
            "created_by",
        ):
            self.assertNotIn(private_name, serialized)
        self.assertIn("private, no-store", detail.headers["Cache-Control"])

    def test_draft_detail_is_not_exposed(self):
        draft = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Private Draft",
        )

        with self._settings():
            response = self.client.get(
                f"/internal/careerhub/v1/jobs/fluxops/{draft.public_slug}/",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )

        self.assertEqual(response.status_code, 404)

    def test_disabled_boundary_fails_closed(self):
        with self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            CAREERHUB_INTERNAL_API_ENABLED=False,
            CAREERHUB_INTERNAL_API_TOKEN=TOKEN,
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        ):
            response = self.client.get(
                "/internal/careerhub/v1/jobs/",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )

        self.assertEqual(response.status_code, 404)
