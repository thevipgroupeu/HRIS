import json
import runpy
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import RefreshToken

from base.models import CompanyGroupAssignment
from base.views import protected_media
from employee.models import Policy, PolicyMultipleFile
from horilla.testkit import make_company, make_employee, make_user
from horilla_api.api_views.recruitment.views import (
    CandidateDocumentGetCreateAPIView,
    CandidateDocumentGetUpdateDeleteAPIView,
    CandidateGetCreateAPIView,
    CandidateGetUpdateDeleteAPIView,
)
from recruitment.models import CandidateDocument
from tvg_careers.application_api import application_status, withdraw_application
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.access import can_download_media
from tvg_erasmus.scanning import FakeCleanScanner


class ProductionConfigurationTests(SimpleTestCase):
    def test_unsafe_private_configuration_is_rejected(self):
        from horilla.settings.security import validate_production_secrets

        safe = "synthetic-validation-key-only-" * 3
        for key, hosts in (
            ("x", ["people.example.invalid"]),
            ("x" * 50, ["people.example.invalid"]),
            (safe, ["people.example.invalid", "*"]),
        ):
            with self.subTest(hosts=hosts, key_length=len(key)), self.assertRaises(
                ImproperlyConfigured
            ):
                validate_production_secrets(key, hosts, "synthetic-init")
        validate_production_secrets(safe, ["people.example.invalid"], "synthetic-init")

    def test_production_careerhub_cannot_enable_debug(self):
        import os

        with patch.dict(
            os.environ, {"DEBUG": "1", "HORILLA_ENV": "production"}, clear=True
        ):
            with self.assertRaises(ImproperlyConfigured):
                runpy.run_path(
                    str(
                        Path(__file__).resolve().parents[2]
                        / "horilla/settings/careerhub.py"
                    )
                )

    def test_fake_scanner_fails_closed_outside_explicit_local_environments(self):
        for env in ("production", "staging", ""):
            with self.subTest(env=env), override_settings(
                HORILLA_ENV=env
            ), self.assertRaises(ImproperlyConfigured):
                FakeCleanScanner().scan(
                    content=b"synthetic",
                    filename="test.pdf",
                    content_type="application/pdf",
                    sha256="0" * 64,
                )


class RecruitmentBoundaryTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Release Employer")
        self.foreign = make_company("Synthetic Other Employer", hq=False)
        self.application = make_application(
            opening=make_job_opening(company=self.company)
        )
        self.other = make_application(opening=make_job_opening(company=self.foreign))
        self.user = make_user("synthetic-release-reader")
        self.employee = make_employee(
            company=self.company, user=self.user, email="reader@example.invalid"
        )

    def grant(self, codename):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="recruitment", codename=codename
            )
        )

    def read(self, view, pk):
        request = APIRequestFactory().get("/synthetic-read/")
        force_authenticate(request, user=self.user)
        return view.as_view()(request, pk=pk)

    def test_candidate_detail_obeys_permission_and_company_in_both_routes(self):
        for view in (CandidateGetCreateAPIView, CandidateGetUpdateDeleteAPIView):
            self.assertEqual(
                self.read(view, self.application.candidate_id).status_code, 404
            )
        self.grant("view_candidate")
        for view in (CandidateGetCreateAPIView, CandidateGetUpdateDeleteAPIView):
            self.assertEqual(
                self.read(view, self.application.candidate_id).status_code, 200
            )
            self.assertEqual(self.read(view, self.other.candidate_id).status_code, 404)
        self.employee.is_active = False
        self.employee.save(update_fields=("is_active",))
        self.assertEqual(
            self.read(
                CandidateGetUpdateDeleteAPIView, self.application.candidate_id
            ).status_code,
            404,
        )

    def test_document_detail_requires_separate_permission_and_correct_company(self):
        own = CandidateDocument._base_manager.create(
            candidate_id=self.application.candidate, title="Synthetic CV"
        )
        other = CandidateDocument._base_manager.create(
            candidate_id=self.other.candidate, title="Synthetic other CV"
        )
        self.grant("view_candidate")
        for view in (
            CandidateDocumentGetCreateAPIView,
            CandidateDocumentGetUpdateDeleteAPIView,
        ):
            self.assertEqual(self.read(view, own.pk).status_code, 404)
        self.grant("view_candidatedocument")
        for view in (
            CandidateDocumentGetCreateAPIView,
            CandidateDocumentGetUpdateDeleteAPIView,
        ):
            self.assertEqual(self.read(view, own.pk).status_code, 200)
            self.assertEqual(self.read(view, other.pk).status_code, 404)

    @override_settings(COMPANY_SCOPED_PERMISSIONS=True)
    def test_group_permission_does_not_bleed_into_another_assigned_company(self):
        reviewer = Group.objects.create(name="Synthetic recruitment reviewer")
        reviewer.permissions.add(
            Permission.objects.get(
                content_type__app_label="recruitment", codename="view_candidate"
            )
        )
        unrelated = Group.objects.create(name="Synthetic unrelated role")
        CompanyGroupAssignment.objects.create(
            user=self.user, company=self.company, group=reviewer
        )
        CompanyGroupAssignment.objects.create(
            user=self.user, company=self.foreign, group=unrelated
        )
        self.assertEqual(
            self.read(
                CandidateGetUpdateDeleteAPIView, self.application.candidate_id
            ).status_code,
            200,
        )
        self.assertEqual(
            self.read(
                CandidateGetUpdateDeleteAPIView, self.other.candidate_id
            ).status_code,
            404,
        )

    def test_native_cv_download_is_object_authorized_and_not_cached(self):
        candidate = self.application.candidate
        candidate.resume.save(
            "synthetic.pdf", ContentFile(b"%PDF-1.4\nSynthetic only\n%%EOF")
        )
        path = candidate.resume.name
        self.addCleanup(candidate.resume.delete, save=False)
        request = RequestFactory().get("/media/" + path)
        request.user = self.user
        self.assertEqual(protected_media(request, path).status_code, 404)
        self.grant("view_candidate")
        response = protected_media(request, path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        with patch("django.http.response.signals.request_finished.send"):
            response.close()
        jwt_request = RequestFactory().get(
            "/media/" + path,
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}",
        )
        jwt_request.user = AnonymousUser()
        jwt_response = protected_media(jwt_request, path)
        self.assertEqual(jwt_response.status_code, 200)
        with patch("django.http.response.signals.request_finished.send"):
            jwt_response.close()
        foreign_reader = make_user("synthetic-foreign-reader")
        make_employee(
            company=self.foreign,
            user=foreign_reader,
            email="foreign-reader@example.invalid",
        )
        foreign_reader.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="recruitment", codename="view_candidate"
            )
        )
        request.user = foreign_reader
        self.assertEqual(protected_media(request, path).status_code, 404)
        self.assertFalse(can_download_media(self.user, "unowned-file.pdf"))

    def bridge(self, app, *, withdraw=False):
        data = {"person_public_id": str(app.person.public_id)}
        if withdraw:
            request = RequestFactory().post(
                "/synthetic/",
                data=json.dumps(data),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer synthetic-only",
            )
            return withdraw_application(request, app.public_id)
        request = RequestFactory().get(
            "/synthetic/", data, HTTP_AUTHORIZATION="Bearer synthetic-only"
        )
        return application_status(request)

    def test_policy_attachment_requires_company_and_audience_membership(self):
        attachment = PolicyMultipleFile._base_manager.create(
            attachment="employee/policies/synthetic.pdf"
        )
        policy = Policy._base_manager.create(
            title="Synthetic restricted policy",
            body="Synthetic only",
            is_visible_to_all=False,
        )
        policy.company_id.add(self.company)
        policy.attachments.add(attachment)
        self.assertFalse(can_download_media(self.user, attachment.attachment.name))
        policy.employees.add(self.employee)
        self.assertTrue(can_download_media(self.user, attachment.attachment.name))
        policy.company_id.set([self.foreign])
        self.assertFalse(can_download_media(self.user, attachment.attachment.name))

    @override_settings(
        CAREERHUB_APPLICATION_API_ENABLED=True,
        CAREERHUB_APPLICATION_API_TOKEN="synthetic-only",
    )
    def test_bridge_scope_and_idempotent_withdrawal(self):
        with override_settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            self.assertEqual(
                len(json.loads(self.bridge(self.application).content)["results"]), 1
            )
            self.assertEqual(json.loads(self.bridge(self.other).content)["results"], [])
            self.assertEqual(self.bridge(self.other, withdraw=True).status_code, 404)
            self.other.refresh_from_db()
            self.assertIsNone(self.other.withdrawn_at)
            self.assertEqual(
                self.bridge(self.application, withdraw=True).status_code, 200
            )
            self.application.refresh_from_db()
            withdrawn = self.application.withdrawn_at
            self.assertEqual(
                self.bridge(self.application, withdraw=True).status_code, 200
            )
            self.application.refresh_from_db()
            self.assertEqual(self.application.withdrawn_at, withdrawn)
        with override_settings(TVG_LEGAL_EMPLOYER_ID=None):
            self.assertEqual(
                json.loads(self.bridge(self.application).content)["results"], []
            )
            self.assertEqual(
                self.bridge(self.application, withdraw=True).status_code, 404
            )
