from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from horilla.testkit import make_company
from tvg_careers.models import JobOpening
from tvg_careers.public_catalog import get_public_opening, list_public_openings
from tvg_careers.tests.factories import make_job_opening
from tvg_core.tests.factories import make_venture


class PublicJobCatalogTests(TestCase):
    def setUp(self):
        self.company = make_company("THEVIPGROUP Sp. z o.o.")
        self.fluxops = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )

    def _payload(self, filters=None):
        with self.settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            return list_public_openings(filters or {})

    def test_projection_contains_safe_role_fields_only(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            is_published=True,
            opportunity_type=JobOpening.OpportunityType.INTERNSHIP,
        )

        payload = self._payload()

        self.assertEqual(payload["count"], 1)
        job = payload["results"][0]
        self.assertEqual(job["slug"], opening.public_slug)
        self.assertTrue(job["accepting_applications"])
        self.assertEqual(job["venture"]["slug"], "fluxops")
        self.assertNotIn("id", job)
        self.assertNotIn("recruitment_id", job)
        self.assertNotIn("hiring_managers", job)
        self.assertNotIn("candidate", job)
        self.assertNotIn("employee", job)

    def test_draft_closed_and_other_employer_roles_are_excluded(self):
        draft = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            title="Synthetic Draft",
        )
        closed = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            title="Synthetic Closed",
            is_published=True,
        )
        closed.recruitment.closed = True
        closed.recruitment.is_published = False
        closed.recruitment.save()
        other_company = make_company("Synthetic Other Employer", hq=False)
        other_venture = make_venture(
            company=other_company,
            name="Other Venture",
            slug="other-venture",
        )
        make_job_opening(
            company=other_company,
            venture=other_venture,
            title="Synthetic Other Tenant",
            is_published=True,
        )

        payload = self._payload()

        self.assertEqual(payload["results"], [])
        with self.settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            with self.assertRaises(JobOpening.DoesNotExist):
                get_public_opening(
                    venture_slug="fluxops",
                    opening_slug=draft.public_slug,
                )

    def test_filters_cover_venture_department_category_mode_erasmus_and_keywords(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            title="Synthetic Python Factory Internship",
            is_published=True,
            opportunity_type=JobOpening.OpportunityType.INTERNSHIP,
            work_mode=JobOpening.WorkMode.HYBRID,
            erasmus_plus_applicable=True,
            work_location="Kraków, Poland",
        )
        department = opening.department.department

        payload = self._payload(
            {
                "venture": "fluxops",
                "department": department,
                "category": "internship",
                "location": "Kraków",
                "work_mode": "hybrid",
                "erasmus": "true",
                "q": "Python",
                "status": "open",
            }
        )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["slug"], opening.public_slug)
        self.assertEqual(self._payload({"q": "not-present"})["count"], 0)

    def test_published_future_and_expired_roles_never_accept_applications(self):
        upcoming = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            title="Synthetic Upcoming",
            is_published=True,
        )
        today = timezone.localdate()
        upcoming.recruitment.start_date = today + timedelta(days=2)
        upcoming.recruitment.end_date = today + timedelta(days=20)
        upcoming.recruitment.save()

        expired = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            title="Synthetic Expired",
            is_published=True,
        )
        expired.recruitment.start_date = today - timedelta(days=20)
        expired.recruitment.end_date = today - timedelta(days=1)
        expired.recruitment.save()

        upcoming_payload = self._payload({"status": "upcoming"})["results"][0]
        closed_payload = self._payload({"status": "closed"})["results"][0]

        self.assertEqual(upcoming_payload["application_status"], "upcoming")
        self.assertFalse(upcoming_payload["accepting_applications"])
        self.assertEqual(closed_payload["application_status"], "closed")
        self.assertFalse(closed_payload["accepting_applications"])
