from __future__ import annotations

import hashlib
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from asset.models import AssetAssignment
from offboarding.models import Offboarding
from project.models import Project, Task
from tvg_erasmus.document_services import (
    approve_document,
    approve_document_requirement,
    return_signed_document,
    staff_upload_document,
    upload_manual_signed_document,
)
from tvg_erasmus.models import (
    ErasmusCaseDocument,
    ErasmusDocumentRequirement,
    ErasmusDocumentVersion,
)
from tvg_offboarding.models import (
    AccessRevocationTask,
    CompletionCase,
    CompletionDocument,
    CompletionDocumentTemplate,
    CompletionPolicy,
    CompletionRequirement,
    ReferenceLetterRequest,
    RetentionPolicy,
)
from tvg_offboarding.services import (
    approve_access_exception,
    attempt_access_revocation,
    complete_internship,
    complete_manual_access_revocation,
    confirm_administrative_closure,
    generate_completion_document,
    handle_reference_request,
    issue_completion_certificate,
    link_final_monthly_presentation,
    link_final_weekly_report,
    materialize_upcoming_completions,
    record_document_signature,
    record_final_supervisor_feedback,
    refresh_completion_state,
    request_reference_letter,
    return_asset,
    schedule_final_presentation,
    start_completion,
)
from tvg_onboarding.models import InternOnboarding, OnboardingAssetAllocation
from tvg_onboarding.services import allocate_asset
from tvg_onboarding.tests import test_intern_onboarding as onboarding_helpers
from tvg_reporting.models import (
    MonthlyPresentation,
    MonthlyPresentationVersion,
    ReportingPolicy,
    ReportingRubricVersion,
    ReviewOutcome,
    WeeklyReport,
)
from tvg_reporting.services import (
    review_monthly_presentation,
    review_weekly_report,
    save_monthly_draft,
    save_weekly_draft,
    submit_monthly_presentation,
    submit_weekly_report,
)


def synthetic_pdf(name="synthetic-completion.pdf"):
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF",
        content_type="application/pdf",
    )


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    LOGIN_URL="peoplehub:login",
    TVG_OFFBOARDING_REVOCATION_BACKENDS={
        "manual": "tvg_offboarding.integrations.DisabledAccessRevocationProvider",
        "synthetic": "tvg_offboarding.integrations.SyntheticAccessRevocationProvider",
    },
)
class InternshipCompletionTests(TestCase):
    _restore_storage = onboarding_helpers.InternOnboardingTests._restore_storage
    _restore_ack_storage = onboarding_helpers.InternOnboardingTests._restore_ack_storage
    _staff = onboarding_helpers.InternOnboardingTests._staff
    _verify_application = onboarding_helpers.InternOnboardingTests._verify_application
    _select_application = onboarding_helpers.InternOnboardingTests._select_application
    _case_values = onboarding_helpers.InternOnboardingTests._case_values
    _create_case = onboarding_helpers.InternOnboardingTests._create_case
    _approved_case = onboarding_helpers.InternOnboardingTests._approved_case
    _template = onboarding_helpers.InternOnboardingTests._template
    _trigger_ready_case = onboarding_helpers.InternOnboardingTests._trigger_ready_case
    _onboarding = onboarding_helpers.InternOnboardingTests._onboarding
    _approve_and_convert = onboarding_helpers.InternOnboardingTests._approve_and_convert
    _asset = onboarding_helpers.InternOnboardingTests._asset

    def setUp(self):
        onboarding_helpers.InternOnboardingTests.setUp(self)
        permissions = {
            "tvg_offboarding": {
                "start_intern_completion",
                "manage_intern_completion",
                "authorize_completion_document",
                "complete_intern_completion",
                "approve_completion_exception",
                "manage_access_revocation",
                "manage_completion_assets",
                "view_completion_management",
                "approve_completion_template",
                "approve_retention_policy",
            },
            "tvg_erasmus": {
                "manage_case_documents",
                "review_case_documents",
                "approve_case_documents",
                "sign_case_documents",
                "return_case_documents",
                "approve_document_requirement",
            },
            "asset": {"change_assetassignment"},
            "tvg_reporting": {
                "review_weekly_report",
                "review_monthly_presentation",
            },
        }
        for app_label, codenames in permissions.items():
            self.manager_user.user_permissions.add(
                *Permission.objects.filter(
                    content_type__app_label=app_label,
                    codename__in=codenames,
                )
            )
        self.manager_user = type(self.manager_user).objects.get(pk=self.manager_user.pk)

        self.onboarding = self._onboarding()
        self.employee = self._approve_and_convert(self.onboarding)
        self.employee.employee_user_id.is_new_employee = False
        self.employee.employee_user_id.save(update_fields=("is_new_employee",))
        self.allocation = allocate_asset(
            user=self.manager_user,
            onboarding=self.onboarding,
            asset=self._asset(),
        )
        self.onboarding.status = InternOnboarding.Status.ONBOARDED
        self.onboarding.onboarded_by = self.manager
        self.onboarding.onboarded_at = timezone.now()
        self.onboarding.save(update_fields=("status", "onboarded_by", "onboarded_at"))

        native = Offboarding._base_manager.create(
            title="Intern Completion",
            description="Synthetic internship completion process.",
            company_id=self.company,
        )
        self.policy = CompletionPolicy.all_objects.create(
            company=self.company,
            native_offboarding=native,
            access_systems=[
                {
                    "key": "company_email",
                    "label": "Company email",
                    "provider": "manual",
                },
                {"key": "github", "label": "GitHub", "provider": "manual"},
                {"key": "vpn", "label": "VPN", "provider": "manual"},
                {"key": "sso", "label": "SSO", "provider": "manual"},
                {
                    "key": "application",
                    "label": "Application access",
                    "provider": "manual",
                },
            ],
        )
        self.hr_user, self.hr = self._staff(
            "synthetic-completion-hr",
            company=self.company,
            permissions={},
        )
        self.policy.hr_recipients.add(self.hr)
        self._after_requirement(code="attendance-after", required=True)
        self._after_requirement(code="optional-university-confirmation", required=False)
        self._reporting_records()
        self._approved_retention_policies()
        self.templates = self._completion_templates()

        self.completion_media = TemporaryDirectory()
        self.addCleanup(self.completion_media.cleanup)
        fields = (
            CompletionDocument._meta.get_field("pdf_file"),
            AccessRevocationTask._meta.get_field("proof_file"),
            ErasmusDocumentVersion._meta.get_field("file"),
            MonthlyPresentation._meta.get_field("presentation_file"),
            MonthlyPresentationVersion._meta.get_field("presentation_file"),
        )
        self.original_completion_storages = [field.storage for field in fields]
        self.completion_storage_fields = fields
        storage = FileSystemStorage(location=self.completion_media.name)
        for field in fields:
            field.storage = storage
        self.addCleanup(self._restore_completion_storages)

    def _restore_completion_storages(self):
        for field, storage in zip(
            self.completion_storage_fields,
            self.original_completion_storages,
            strict=True,
        ):
            field.storage = storage

    def _after_requirement(self, *, code, required):
        requirement = ErasmusDocumentRequirement.all_objects.create(
            company=self.company,
            code=code,
            version_number=1,
            title=f"Synthetic {code}",
            document_type=ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT,
            lifecycle_phase=ErasmusDocumentRequirement.Phase.AFTER_INTERNSHIP,
            university=self._case_values()["university"],
            programme=self._case_values()["programme"],
            instructions="Synthetic After Mobility form only.",
            required=required,
            signer_roles=[
                ErasmusDocumentRequirement.SignerRole.TVG_AUTHORIZED_REPRESENTATIVE
            ],
        )
        return approve_document_requirement(
            user=self.manager_user, requirement=requirement
        )

    def _reporting_records(self):
        ReportingPolicy.all_objects.create(company=self.company)
        self.rubric = ReportingRubricVersion.all_objects.create(
            company=self.company,
            name="Synthetic final rubric",
            version_number=1,
            criteria=[
                {
                    "code": "clarity",
                    "label": "Writing clarity",
                    "dimension": "report_writing",
                    "weight": 40,
                    "max_score": 5,
                },
                {
                    "code": "delivery",
                    "label": "Internship delivery",
                    "dimension": "internship_performance",
                    "weight": 60,
                    "max_score": 5,
                },
            ],
            approved_by=self.manager,
            approved_at=timezone.now(),
        )
        self.project = Project._base_manager.create(
            title="Synthetic Final Project",
            start_date=self.onboarding.start_date,
            end_date=self.onboarding.end_date,
            description="Synthetic completion reporting only.",
            company_id=self.company,
        )
        self.task = Task._base_manager.create(
            title="Synthetic final milestone",
            project=self.project,
            stage=self.project.project_stages.first(),
            status="completed",
            description="Synthetic final evidence.",
        )
        self.task.task_members.add(self.employee)

    def _approved_retention_policies(self):
        for kind, _label in RetentionPolicy.RecordKind.choices:
            RetentionPolicy.all_objects.create(
                company=self.company,
                record_kind=kind,
                name=f"Synthetic {kind} retention",
                version_number=1,
                action=RetentionPolicy.Action.REVIEW,
                retention_days=365,
                approved_by=self.manager,
                approved_at=timezone.now(),
            )

    def _completion_templates(self):
        rows = {}
        definitions = (
            (
                CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE,
                [CompletionDocumentTemplate.SignerRole.COMPANY_AUTHORIZED],
                "{company_name} confirms {employee_name} ({intern_id}) completed the internship from {internship_start_date} to {internship_end_date}.",
            ),
            (
                CompletionDocumentTemplate.Kind.ASSET_RETURN,
                [
                    CompletionDocumentTemplate.SignerRole.COMPANY_AUTHORIZED,
                    CompletionDocumentTemplate.SignerRole.INTERN,
                ],
                "{employee_name} and {company_name} confirm the following return: {asset_summary}",
            ),
            (
                CompletionDocumentTemplate.Kind.REFERENCE_LETTER,
                [CompletionDocumentTemplate.SignerRole.COMPANY_AUTHORIZED],
                "{company_name} provides this approved reference for {employee_name}.",
            ),
        )
        for kind, roles, body in definitions:
            rows[kind] = CompletionDocumentTemplate.all_objects.create(
                company=self.company,
                kind=kind,
                name=f"Synthetic {kind}",
                version_number=1,
                body=body,
                required_signer_roles=roles,
                approved_by=self.manager,
                approved_at=timezone.now(),
            )
        return rows

    def _weekly_and_monthly(self):
        week = self.onboarding.end_date
        week -= timedelta(days=week.weekday())
        weekly = save_weekly_draft(
            employee=self.employee,
            reporting_week=week,
            assigned_project=self.project,
            planned_objectives="Complete final objectives.",
            completed_tasks="Completed final tasks.",
            evidence_links=["https://example.test/synthetic-final-evidence"],
            blockers="None.",
            learning_outcomes="Synthetic learning outcome.",
            next_week_plan="Close the internship.",
            hours=35,
            progress_percent=100,
        )
        weekly_version = submit_weekly_report(
            employee=self.employee, report=weekly, task_ids=(self.task.pk,)
        )
        review_weekly_report(
            user=self.manager_user,
            version=weekly_version,
            outcome=ReviewOutcome.APPROVED,
            comments="Synthetic final weekly review.",
            rubric=self.rubric,
            rubric_scores={"clarity": 4, "delivery": 5},
        )

        content = b"%PDF-1.4\nsynthetic presentation\n%%EOF"
        presentation = save_monthly_draft(
            employee=self.employee,
            presentation_month=self.onboarding.end_date.replace(day=1),
            assigned_project=self.project,
            presentation_date=self.onboarding.end_date,
            project_milestones="Synthetic final milestones.",
            file_metadata={
                "file": SimpleUploadedFile(
                    "synthetic-final-presentation.pdf",
                    content,
                    content_type="application/pdf",
                ),
                "original_filename": "synthetic-final-presentation.pdf",
                "content_type": "application/pdf",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            },
        )
        presentation_version = submit_monthly_presentation(
            employee=self.employee, presentation=presentation
        )
        review_monthly_presentation(
            user=self.manager_user,
            version=presentation_version,
            outcome=ReviewOutcome.APPROVED,
            comments="Synthetic final presentation review.",
            rubric=self.rubric,
            rubric_scores={"clarity": 4, "delivery": 5},
        )
        weekly.refresh_from_db()
        presentation.refresh_from_db()
        return weekly, presentation

    def _resolve_after_mobility(self, case):
        mandatory = case.onboarding.erasmus_case.documents.get(required=True)
        staff_upload_document(
            user=self.manager_user,
            document=mandatory,
            upload=synthetic_pdf("synthetic-after-mobility.pdf"),
        )
        approve_document(user=self.manager_user, document=mandatory)
        upload_manual_signed_document(
            user=self.manager_user,
            document=mandatory,
            upload=synthetic_pdf("synthetic-after-mobility-signed.pdf"),
            signed_roles=mandatory.required_signer_roles,
        )
        return_signed_document(user=self.manager_user, document=mandatory)
        mandatory.refresh_from_db()
        return mandatory

    def _resolve_access(self, case):
        for task in case.access_revocations.all():
            task = attempt_access_revocation(user=self.manager_user, task=task)
            self.assertEqual(task.status, AccessRevocationTask.Status.MANUAL_REQUIRED)
            complete_manual_access_revocation(
                user=self.manager_user,
                task=task,
                proof_reference=f"SYNTHETIC-IT-{task.system_key.upper()}",
            )

    def _prepare_and_sign(self, case, kind):
        document = generate_completion_document(
            user=self.manager_user,
            case=case,
            template=self.templates[kind],
            issue_date=self.onboarding.end_date,
        )
        for role in document.required_signer_roles:
            user = (
                self.employee.employee_user_id
                if role == CompletionDocumentTemplate.SignerRole.INTERN
                else self.manager_user
            )
            record_document_signature(
                user=user,
                document=document,
                signer_role=role,
                evidence_reference=f"SYNTHETIC-SIGN-{role}",
            )
        document.refresh_from_db()
        return document

    def test_incomplete_offboarding_and_outstanding_assets_block_completion(self):
        case = start_completion(user=self.manager_user, onboarding=self.onboarding)
        self.assertEqual(case.state, CompletionCase.State.AWAITING_ASSETS)
        with patch(
            "tvg_offboarding.services.timezone.localdate",
            return_value=self.onboarding.end_date,
        ):
            with self.assertRaisesMessage(ValidationError, "Mandatory completion"):
                complete_internship(user=self.manager_user, case=case)
        self.assertTrue(
            AssetAssignment._base_manager.filter(
                assigned_to_employee_id=self.employee, return_date__isnull=True
            ).exists()
        )

    def test_access_revocation_requires_proof_and_is_idempotent(self):
        case = start_completion(user=self.manager_user, onboarding=self.onboarding)
        task = attempt_access_revocation(
            user=self.manager_user, task=case.access_revocations.first()
        )
        repeated = attempt_access_revocation(user=self.manager_user, task=task)
        self.assertEqual(repeated.pk, task.pk)
        self.assertEqual(repeated.manual_task.pk, task.manual_task.pk)
        with self.assertRaisesMessage(ValidationError, "proof"):
            complete_manual_access_revocation(user=self.manager_user, task=task)
        excepted = case.access_revocations.exclude(pk=task.pk).first()
        exception = approve_access_exception(
            user=self.manager_user,
            task=excepted,
            reason="Synthetic authorized access exception.",
        )
        excepted.refresh_from_db()
        self.assertEqual(excepted.status, AccessRevocationTask.Status.EXCEPTED)
        self.assertEqual(exception.approved_by_id, self.manager.pk)

    def test_approaching_end_creates_upcoming_case_and_notifies_hr_and_supervisor(self):
        cases = materialize_upcoming_completions(
            as_of=self.onboarding.end_date - timedelta(days=30)
        )
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.state, CompletionCase.State.UPCOMING)
        self.assertIsNone(case.started_at)
        self.assertSetEqual(
            set(case.notifications.values_list("recipient_id", flat=True)),
            {self.manager.pk, self.hr.pk},
        )

    def test_optional_university_form_and_reference_request_do_not_block(self):
        case = start_completion(user=self.manager_user, onboarding=self.onboarding)
        optional = case.onboarding.erasmus_case.documents.get(required=False)
        self.assertEqual(optional.status, ErasmusCaseDocument.Status.REQUESTED)
        reference = request_reference_letter(
            user=self.employee.employee_user_id, case=case
        )
        handle_reference_request(
            user=self.manager_user,
            request=reference,
            status=ReferenceLetterRequest.Status.DECLINED,
        )
        refresh_completion_state(case=case, actor=self.manager)
        reference.refresh_from_db()
        requirement = case.requirements.get(
            code=CompletionRequirement.Code.UNIVERSITY_DOCUMENTS
        )
        self.assertTrue(requirement.required)
        self.assertEqual(requirement.status, CompletionRequirement.Status.PENDING)
        self.assertEqual(reference.status, ReferenceLetterRequest.Status.DECLINED)

    def test_cross_company_actor_cannot_start_completion(self):
        outsider_user, _outsider = self._staff(
            "synthetic-completion-outsider",
            company=onboarding_helpers.make_company("Synthetic Completion Outsider"),
            permissions={
                "tvg_offboarding": {"start_intern_completion"},
            },
        )
        with self.assertRaises(PermissionDenied):
            start_completion(user=outsider_user, onboarding=self.onboarding)

    def test_peoplehub_case_and_download_require_object_access(self):
        case = start_completion(user=self.manager_user, onboarding=self.onboarding)
        document = generate_completion_document(
            user=self.manager_user,
            case=case,
            template=self.templates[
                CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE
            ],
            issue_date=self.onboarding.end_date,
        )
        detail_url = reverse("completion:detail", args=(case.public_id,))
        download_url = reverse(
            "completion:document-download",
            args=(case.public_id, document.public_id),
        )
        client = Client()
        self.assertEqual(client.get(detail_url).status_code, 302)

        peer_user, _peer = self._staff(
            "synthetic-completion-peer",
            company=self.company,
            permissions={},
        )
        client.force_login(peer_user)
        self.assertEqual(client.get(detail_url).status_code, 403)
        self.assertEqual(client.get(download_url).status_code, 403)

        client.force_login(self.employee.employee_user_id)
        self.assertEqual(client.get(detail_url).status_code, 200)
        response = client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        with patch("django.http.response.signals.request_finished.send"):
            response.close()

    def test_full_synthetic_internship_offboarding_flow(self):
        case = start_completion(user=self.manager_user, onboarding=self.onboarding)
        weekly, presentation = self._weekly_and_monthly()
        schedule_final_presentation(
            user=self.manager_user,
            case=case,
            scheduled_for=timezone.now() + timedelta(days=1),
        )
        link_final_weekly_report(user=self.manager_user, case=case, report=weekly)
        link_final_monthly_presentation(
            user=self.manager_user, case=case, presentation=presentation
        )
        record_final_supervisor_feedback(
            user=self.manager_user,
            case=case,
            comments="Synthetic final supervisor feedback.",
            task_completion_evidence="Synthetic approved report evidence.",
            development_feedback="Synthetic development feedback.",
            writing_quality_comments="Writing quality is recorded separately.",
            internship_performance_comments="Internship delivery met expectations.",
            rubric=self.rubric,
            rubric_scores={"clarity": 4, "delivery": 5},
        )
        mandatory = self._resolve_after_mobility(case)
        self.assertEqual(mandatory.status, ErasmusCaseDocument.Status.RETURNED)

        assignment = self.allocation.native_assignment
        with patch(
            "tvg_offboarding.services.timezone.localdate",
            return_value=self.onboarding.end_date,
        ):
            return_asset(
                user=self.manager_user,
                case=case,
                assignment=assignment,
                return_date=self.onboarding.end_date,
                return_status="Healthy",
                return_condition="Synthetic laptop returned in good condition.",
            )
        asset_document = self._prepare_and_sign(
            case, CompletionDocumentTemplate.Kind.ASSET_RETURN
        )
        self.assertEqual(asset_document.status, CompletionDocument.Status.SIGNED)
        self._resolve_access(case)

        certificate_v1 = self._prepare_and_sign(
            case, CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE
        )
        certificate_v2 = self._prepare_and_sign(
            case, CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE
        )
        self.assertEqual(certificate_v2.version_number, 2)
        with self.assertRaisesMessage(ValidationError, "latest"):
            issue_completion_certificate(
                user=self.manager_user, document=certificate_v1
            )
        issue_completion_certificate(user=self.manager_user, document=certificate_v2)

        reference = request_reference_letter(
            user=self.employee.employee_user_id, case=case
        )
        handle_reference_request(
            user=self.manager_user,
            request=reference,
            status=ReferenceLetterRequest.Status.APPROVED,
        )
        self._prepare_and_sign(case, CompletionDocumentTemplate.Kind.REFERENCE_LETTER)

        pending = list(
            case.requirements.filter(
                required=True, status=CompletionRequirement.Status.PENDING
            ).values_list("code", flat=True)
        )
        self.assertEqual(pending, [CompletionRequirement.Code.ADMINISTRATIVE_CLOSURE])
        confirm_administrative_closure(user=self.manager_user, case=case)
        with patch(
            "tvg_offboarding.services.timezone.localdate",
            return_value=self.onboarding.end_date,
        ):
            completed = complete_internship(user=self.manager_user, case=case)
        completed.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(completed.state, CompletionCase.State.COMPLETED)
        self.assertFalse(self.employee.is_active)
        self.assertTrue(
            completed.events.filter(event_type="completion.completed").exists()
        )
        self.assertTrue(completed.retention_schedules.exists())
        self.assertTrue(completed.onboarding.erasmus_case.application_id)
        self.assertTrue(completed.documents.exists())
