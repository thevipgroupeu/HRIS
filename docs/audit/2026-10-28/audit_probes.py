"""Reproductions of audit findings using synthetic data only."""

import json
from tempfile import TemporaryDirectory

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import RequestFactory, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from base.views import protected_media
from horilla.testkit import make_company, make_employee, make_user
from horilla_api.api_views.recruitment.views import CandidateGetUpdateDeleteAPIView
from tvg_careers.application_api import application_status, withdraw_application
from tvg_careers.tests.factories import make_application, make_job_opening


class ProductionSecurityProbes(TestCase):
    def test_native_candidate_detail_requires_recruitment_object_access(self):
        company = make_company("Synthetic Candidate API Tenant")
        application = make_application(opening=make_job_opening(company=company))
        user = make_user("synthetic.api.unrelated.employee")
        make_employee(company=company, user=user, email="api-unrelated@example.invalid")
        self.assertFalse(user.has_perm("recruitment.view_candidate"))
        request = APIRequestFactory().get("/synthetic-candidate-api/")
        force_authenticate(request, user=user)
        response = CandidateGetUpdateDeleteAPIView.as_view()(
            request, pk=application.candidate_id
        )
        print(
            f"AUDIT: low-privilege employee native Candidate detail returned HTTP {response.status_code}; expected 403/404"
        )
        self.assertIn(response.status_code, (403, 404))

    def test_native_document_download_requires_object_authorization(self):
        company = make_company("Synthetic Audit Employer")
        user = make_user("synthetic.unrelated.employee")
        make_employee(company=company, user=user, email="unrelated@example.invalid")
        with TemporaryDirectory(prefix="synthetic-media-probe-") as tmp:
            storage = FileSystemStorage(location=tmp)
            name = storage.save(
                "recruitment/resume/synthetic-other-person.pdf",
                ContentFile(b"%PDF-1.4\nSynthetic private CV\n%%EOF"),
            )
            request = RequestFactory().get("/media/" + name)
            request.user = user
            with self.settings(MEDIA_ROOT=tmp):
                response = protected_media(request, name)
                status = response.status_code
                response.close()
            print(
                f"AUDIT: unrelated authenticated employee CV download returned HTTP {status}; expected 403/404"
            )
            self.assertIn(
                status,
                (403, 404),
                "Native media permits an unrelated employee to download another person's CV",
            )

    def _foreign_application(self):
        configured = make_company("Synthetic Configured Tenant")
        foreign = make_company("Synthetic Other Tenant")
        application = make_application(opening=make_job_opening(company=foreign))
        return configured, application

    def test_application_status_respects_configured_legal_employer(self):
        company, application = self._foreign_application()
        request = RequestFactory().get(
            "/internal/careerhub/v1/applications/",
            {
                "person_public_id": str(application.person.public_id),
            },
            HTTP_AUTHORIZATION="Bearer synthetic-audit-boundary-only",
        )
        with self.settings(
            TVG_LEGAL_EMPLOYER_ID=company.pk,
            CAREERHUB_APPLICATION_API_ENABLED=True,
            CAREERHUB_APPLICATION_API_TOKEN="synthetic-audit-boundary-only",
        ):
            response = application_status(request)
        rows = json.loads(response.content).get("results", [])
        print(
            f"AUDIT: configured tenant status API returned {len(rows)} foreign-tenant applications; expected zero"
        )
        self.assertEqual(rows, [])

    def test_withdraw_cannot_mutate_another_legal_employer(self):
        company, application = self._foreign_application()
        request = RequestFactory().post(
            "/internal/careerhub/v1/applications/withdraw/",
            data=json.dumps({"person_public_id": str(application.person.public_id)}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer synthetic-audit-boundary-only",
        )
        with self.settings(
            TVG_LEGAL_EMPLOYER_ID=company.pk,
            CAREERHUB_APPLICATION_API_ENABLED=True,
            CAREERHUB_APPLICATION_API_TOKEN="synthetic-audit-boundary-only",
        ):
            response = withdraw_application(request, application.public_id)
        application.refresh_from_db()
        print(
            f"AUDIT: configured tenant withdrawal returned HTTP {response.status_code}; foreign record changed={application.withdrawn_at is not None}; expected 404 and unchanged"
        )
        self.assertIsNone(application.withdrawn_at)
        self.assertEqual(response.status_code, 404)
