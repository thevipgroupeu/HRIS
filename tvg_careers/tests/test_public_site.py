from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings

from tvg_careers.public_client import CareerHubAPIError, CareerHubJobNotFound

PUBLIC_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tvg_careers.public_middleware.PublicSecurityHeadersMiddleware",
]
PUBLIC_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [Path(settings.BASE_DIR) / "tvg_careers" / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": ["django.template.loaders.filesystem.Loader"],
            "context_processors": ["django.template.context_processors.request"],
        },
    }
]

SAMPLE_JOB = {
    "public_id": "e8b43b89-145d-47e9-b1ea-9fe305a03bf5",
    "slug": "synthetic-fluxops-intern",
    "title": "Synthetic FluxOps Software Intern",
    "role_description": "Build and test a supervised factory-floor workflow.",
    "responsibilities": "Map the workflow.\nImplement a tested feature.",
    "learning_outcomes": "Practise Django delivery.\nDocument decisions.",
    "venture": {
        "name": "FluxOps",
        "slug": "fluxops",
        "summary": "Factory-floor software.",
    },
    "legal_employer": "THEVIPGROUP Sp. z o.o.",
    "department": "Product Engineering",
    "job_position": "Software Intern",
    "category": "internship",
    "category_label": "Internship",
    "required_skills": ["Python"],
    "preferred_skills": ["Django"],
    "location": "Kraków, Poland",
    "work_mode": "hybrid",
    "work_mode_label": "Hybrid",
    "erasmus_plus": True,
    "expected_start_date_from": "2027-01-10",
    "expected_start_date_to": "2027-01-24",
    "minimum_duration_weeks": 12,
    "preferred_duration_weeks": 24,
    "number_of_positions": 1,
    "application_opening_date": "2026-10-01",
    "application_closing_date": "2026-12-01",
    "application_status": "open",
    "accepting_applications": True,
    "url_path": "/opportunities/fluxops/synthetic-fluxops-intern/",
}


class FakeJobClient:
    def __init__(self, job=None, error=None):
        self.job = deepcopy(job or SAMPLE_JOB)
        self.error = error
        self.filters = []

    def list_jobs(self, filters=None):
        if self.error:
            raise self.error
        self.filters.append(filters or {})
        return {
            "results": [deepcopy(self.job)],
            "count": 1,
            "facets": {
                "ventures": [["fluxops", "FluxOps"]],
                "departments": ["Product Engineering"],
                "categories": [["internship", "Internship"], ["job", "Job"]],
                "locations": ["Kraków, Poland"],
                "work_modes": [["hybrid", "Hybrid"]],
                "statuses": [["open", "Open now"]],
            },
        }

    def get_job(self, venture_slug, opening_slug):
        if self.error:
            raise self.error
        if (
            venture_slug != self.job["venture"]["slug"]
            or opening_slug != self.job["slug"]
        ):
            raise CareerHubJobNotFound
        return deepcopy(self.job)


@override_settings(
    ROOT_URLCONF="horilla.careerhub_urls",
    MIDDLEWARE=PUBLIC_MIDDLEWARE,
    TEMPLATES=PUBLIC_TEMPLATES,
    CAREERHUB_PUBLIC_BASE_URL="https://career.thevipgroup.eu",
    CAREERHUB_CONTACT_EMAIL="recruitment@example.test",
    X_FRAME_OPTIONS="DENY",
)
class CareerHubPublicSiteTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def test_all_requested_pages_render_and_private_routes_are_not_mounted(self):
        fake = FakeJobClient()
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            paths = (
                "/",
                "/opportunities/",
                "/ventures/fluxops/",
                SAMPLE_JOB["url_path"],
                "/internships/",
                "/erasmus-plus/",
                "/process/",
                "/privacy/",
            )
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)

        for private_path in (
            "/admin/",
            "/employee/",
            "/api/v1/",
            "/internal/careerhub/v1/jobs/",
            "/media/private.pdf",
        ):
            with self.subTest(private_path=private_path):
                self.assertEqual(self.client.get(private_path).status_code, 404)

    def test_listing_forwards_search_filters_and_renders_live_result(self):
        fake = FakeJobClient()
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get(
                "/opportunities/",
                {
                    "venture": "fluxops",
                    "department": "Product Engineering",
                    "category": "internship",
                    "location": "Kraków",
                    "work_mode": "hybrid",
                    "erasmus": "true",
                    "q": "Python",
                    "status": "open",
                    "private": "ignored",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SAMPLE_JOB["title"])
        self.assertNotIn("private", fake.filters[0])
        self.assertEqual(fake.filters[0]["status"], "open")

    def test_detail_has_canonical_metadata_json_ld_and_login_ready_cta(self):
        fake = FakeJobClient()
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get(SAMPLE_JOB["url_path"])
            handoff = self.client.get(
                "/opportunities/fluxops/synthetic-fluxops-intern/apply/"
            )

        self.assertContains(
            response,
            'rel="canonical" href="https://career.thevipgroup.eu/opportunities/fluxops/synthetic-fluxops-intern/"',
            html=False,
        )
        self.assertContains(response, 'type="application/ld+json"', html=False)
        self.assertContains(response, "Start application")
        self.assertEqual(handoff.status_code, 302)
        self.assertTrue(handoff.url.startswith("/candidate/sign-in/?"))

    def test_non_accepting_role_has_no_active_application_handoff(self):
        job = deepcopy(SAMPLE_JOB)
        job["application_status"] = "closed"
        job["accepting_applications"] = False
        fake = FakeJobClient(job=job)
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            detail = self.client.get(job["url_path"])
            handoff = self.client.get(
                "/opportunities/fluxops/synthetic-fluxops-intern/apply/"
            )

        self.assertNotContains(detail, ">Start application<", html=False)
        self.assertContains(detail, "Applications are not open")
        self.assertEqual(
            handoff.url,
            f"{job['url_path']}?application=closed",
        )

    def test_unknown_or_unpublished_detail_is_404(self):
        fake = FakeJobClient(error=CareerHubJobNotFound())
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get("/opportunities/fluxops/not-public/")

        self.assertEqual(response.status_code, 404)

    def test_api_outage_returns_503_without_fallback_job_data(self):
        fake = FakeJobClient(error=CareerHubAPIError("synthetic outage"))
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get("/opportunities/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "No cached or hard-coded job list has been substituted.",
            status_code=503,
        )

    def test_accessibility_and_responsive_contract_is_present(self):
        fake = FakeJobClient()
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get("/opportunities/")

        self.assertContains(response, 'name="viewport"', html=False)
        self.assertContains(response, 'class="skip-link"', html=False)
        self.assertContains(response, 'aria-label="Filter opportunities"', html=False)
        self.assertContains(response, 'for="filter-q"', html=False)
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

        stylesheet = (
            Path(settings.BASE_DIR)
            / "tvg_careers"
            / "static"
            / "careerhub"
            / "site.css"
        ).read_text(encoding="utf-8")
        self.assertIn("@media (min-width: 48rem)", stylesheet)
        self.assertIn("@media (min-width: 72rem)", stylesheet)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)

    def test_thevipgroup_brand_is_used_on_public_pages(self):
        fake = FakeJobClient()
        with patch("tvg_careers.public_views.get_public_job_client", return_value=fake):
            response = self.client.get("/privacy/")

        self.assertContains(response, "THEVIPGROUP CareerHub")
        self.assertContains(
            response,
            "/static/careerhub/branding/thevipgroup-logo.png",
            html=False,
        )
        self.assertNotContains(response, "Horilla administration")
        self.assertNotContains(response, "Horilla recruitment records")

    def test_candidate_placeholder_is_noindex_and_collects_no_credentials(self):
        response = self.client.get(
            "/candidate/sign-in/",
            {"job": SAMPLE_JOB["public_id"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertNotContains(response, "<form", html=False)
        self.assertNotContains(response, 'type="password"', html=False)
