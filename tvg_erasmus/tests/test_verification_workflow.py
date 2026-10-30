from datetime import date, time, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from horilla.testkit import make_company, make_employee, make_user
from recruitment.forms import ScheduleInterviewForm
from recruitment.models import CandidateDocument, InterviewSchedule, Stage
from tvg_careers.application_api import _safe_projection
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture
from tvg_erasmus.models import (
    ScreeningDecision,
    VerificationAuditEvent,
    VerificationCase,
    VerificationChecklist,
    VerificationEvidence,
    VerificationNotification,
    VerificationRequirement,
    VerificationRequirementTemplate,
)
from tvg_erasmus.services import (
    close_verification_for_inactive_application,
    interview_eligibility,
    purge_expired_verification_evidence,
    queue_requirement_reminder,
    review_requirement,
    screen_application,
    submit_evidence,
    verification_projection,
    visible_applications,
)

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
VERIFICATION_TOKEN = "synthetic-verification-token-000000000000"


@override_settings(
    TVG_VERIFICATION_SCANNER_BACKEND="tvg_erasmus.scanning.FakeCleanScanner"
)
class VerificationWorkflowTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.addCleanup(self.private_media.cleanup)
        self.file_field = VerificationEvidence._meta.get_field("file")
        self.original_storage = self.file_field.storage
        self.file_field.storage = FileSystemStorage(location=self.private_media.name)
        self.addCleanup(self._restore_storage)

        self.company = make_company("Synthetic Verification Employer")
        self.venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="synthetic-verification-fluxops",
        )
        self.opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            is_published=True,
            title="Synthetic Erasmus Verification Role",
        )
        self.person = make_person(
            company=self.company,
            email="verification.applicant@example.test",
        )
        self.application = make_application(
            opening=self.opening,
            person=self.person,
            email=self.person.primary_email,
        )
        self.user, self.employee = self._make_staff(
            "verification-manager",
            company=self.company,
            manager=True,
            permissions=(
                "view_screeningdecision",
                "screen_application",
                "manage_verification",
                "review_verification",
                "send_verification_reminder",
            ),
        )
        self.checklist = self._make_checklist(self.opening)

    def _restore_storage(self):
        self.file_field.storage = self.original_storage

    def _make_staff(self, username, *, company, manager, permissions):
        user = make_user(username, email=f"{username}@example.test")
        employee = make_employee(
            company=company,
            email=f"{username}.employee@example.test",
            user=user,
        )
        if manager:
            self.opening.recruitment.recruitment_managers.add(employee)
        grants = Permission.objects.filter(
            content_type__app_label="tvg_erasmus",
            codename__in=permissions,
        )
        user.user_permissions.add(*grants)
        user = type(user).objects.get(pk=user.pk)
        employee = type(employee).objects.get(pk=employee.pk)
        return user, employee

    def _make_checklist(self, opening):
        checklist = VerificationChecklist.all_objects.create(
            recruitment=opening.recruitment,
            retention_days=30,
        )
        rows = (
            ("identity", VerificationRequirementTemplate.Category.IDENTITY),
            ("student-status", VerificationRequirementTemplate.Category.STUDENT_STATUS),
            ("university", VerificationRequirementTemplate.Category.UNIVERSITY),
        )
        for sequence, (code, category) in enumerate(rows, start=1):
            VerificationRequirementTemplate.all_objects.create(
                checklist=checklist,
                code=code,
                category=category,
                title=f"Synthetic {code}",
                instructions="Upload a synthetic verification document.",
                required=True,
                allowed_formats=["pdf"],
                max_size_mb=1,
                sequence=sequence,
            )
        return checklist

    def _shortlist(self, application=None):
        application = application or self.application
        screen_application(
            user=self.user,
            application=application,
            action=ScreeningDecision.Status.SHORTLISTED,
        )
        return VerificationCase.all_objects.get(application=application)

    def _upload(self, requirement, *, person=None, name="synthetic-proof.pdf"):
        return submit_evidence(
            application=requirement.case.application,
            requirement_public_id=requirement.public_id,
            person_public_id=(person or requirement.case.application.person).public_id,
            upload=SimpleUploadedFile(name, PDF, content_type="application/pdf"),
        )

    def _verify(self, requirement):
        review_requirement(
            user=self.user,
            requirement=requirement,
            decision=VerificationCase.Status.UNDER_REVIEW,
        )
        return review_requirement(
            user=self.user,
            requirement=requirement,
            decision=VerificationCase.Status.VERIFIED,
        )

    def test_screening_actions_and_required_checklist_snapshot(self):
        information = screen_application(
            user=self.user,
            application=self.application,
            action=ScreeningDecision.Status.INFORMATION_REQUESTED,
            applicant_message="Please clarify your synthetic availability.",
            internal_note="Synthetic internal note.",
        )
        self.assertEqual(
            information.status, ScreeningDecision.Status.INFORMATION_REQUESTED
        )
        self.assertEqual(
            verification_projection(self.application)["next_action"],
            "Please clarify your synthetic availability.",
        )

        hold = screen_application(
            user=self.user,
            application=self.application,
            action=ScreeningDecision.Status.HOLD,
        )
        self.assertEqual(hold.status, ScreeningDecision.Status.HOLD)
        case = self._shortlist()

        self.assertEqual(case.status, VerificationCase.Status.REQUESTED)
        self.assertEqual(case.requirements.count(), 3)
        self.assertEqual(
            set(case.requirements.values_list("category", flat=True)),
            {
                VerificationRequirementTemplate.Category.IDENTITY,
                VerificationRequirementTemplate.Category.STUDENT_STATUS,
                VerificationRequirementTemplate.Category.UNIVERSITY,
            },
        )
        self.assertEqual(
            CandidateDocument.objects.filter(
                candidate_id=self.application.candidate
            ).count(),
            3,
        )
        self.assertEqual(
            VerificationNotification.all_objects.filter(case=case).count(), 1
        )

    def test_incomplete_erasmus_checklist_fails_closed_and_rolls_back_screening(self):
        VerificationRequirementTemplate.all_objects.filter(
            checklist=self.checklist,
            category=VerificationRequirementTemplate.Category.UNIVERSITY,
        ).delete()
        with self.assertRaisesMessage(ValidationError, "must require identity"):
            self._shortlist()
        self.assertFalse(
            ScreeningDecision.all_objects.filter(application=self.application).exists()
        )
        self.assertFalse(
            VerificationCase.all_objects.filter(application=self.application).exists()
        )

    def test_missing_documents_block_interview_schedule_and_stage_change(self):
        self._shortlist()
        form = ScheduleInterviewForm(
            data={
                "candidate_id": self.application.candidate_id,
                "employee_id": [self.employee.pk],
                "interview_date": (date.today() + timedelta(days=1)).isoformat(),
                "interview_time": "10:00",
                "description": "Synthetic interview",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("candidate_id", form.errors)
        with self.assertRaisesMessage(ValidationError, "verification is incomplete"):
            InterviewSchedule.objects.create(
                candidate_id=self.application.candidate,
                interview_date=date.today() + timedelta(days=1),
                interview_time=time(10, 0),
                description="Synthetic interview",
            )

        interview_stage = Stage.objects.create(
            recruitment_id=self.opening.recruitment,
            stage="Synthetic gated interview",
            stage_type="interview",
            sequence=20,
        )
        candidate = self.application.candidate
        candidate.stage_id = interview_stage
        with self.assertRaisesMessage(ValidationError, "verification is incomplete"):
            candidate.save()

    def test_corrected_submission_is_versioned_and_all_approvals_enable_interview(self):
        case = self._shortlist()
        requirements = list(case.requirements.all())
        first = requirements[0]
        evidence_v1 = self._upload(first)
        review_requirement(
            user=self.user,
            requirement=first,
            decision=VerificationCase.Status.UNDER_REVIEW,
        )
        review_requirement(
            user=self.user,
            requirement=first,
            decision=VerificationCase.Status.REVISION_NEEDED,
            reason="Upload a clearer synthetic page.",
        )
        evidence_v2 = self._upload(first, name="synthetic-proof-corrected.pdf")
        evidence_v1.refresh_from_db()
        self.assertIsNotNone(evidence_v1.superseded_at)
        self.assertEqual(evidence_v2.version_number, 2)
        self._verify(first)

        for requirement in requirements[1:]:
            self._upload(requirement)
            self._verify(requirement)

        case.refresh_from_db()
        self.assertEqual(case.status, VerificationCase.Status.VERIFIED)
        self.assertTrue(interview_eligibility(self.application).eligible)
        interview = InterviewSchedule.objects.create(
            candidate_id=self.application.candidate,
            interview_date=date.today() + timedelta(days=1),
            interview_time=time(10, 0),
            description="Synthetic eligible interview",
        )
        self.assertIsNotNone(interview.pk)

    def test_rejected_evidence_is_terminal_and_needs_candidate_reason(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        self._upload(requirement)
        review_requirement(
            user=self.user,
            requirement=requirement,
            decision=VerificationCase.Status.UNDER_REVIEW,
        )
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            review_requirement(
                user=self.user,
                requirement=requirement,
                decision=VerificationCase.Status.REJECTED,
            )
        review_requirement(
            user=self.user,
            requirement=requirement,
            decision=VerificationCase.Status.REJECTED,
            reason="Synthetic document could not be verified.",
        )
        case.refresh_from_db()
        self.assertEqual(case.status, VerificationCase.Status.REJECTED)
        self.assertEqual(case.reviewed_by, self.employee)
        self.assertIsNotNone(case.reviewed_at)
        with self.assertRaisesMessage(ValidationError, "not accepting"):
            self._upload(requirement, name="late-replacement.pdf")

    def test_unscanned_evidence_cannot_be_reviewed(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        with override_settings(
            TVG_VERIFICATION_SCANNER_BACKEND="tvg_erasmus.scanning.DisabledScanner"
        ):
            evidence = self._upload(requirement)
        self.assertEqual(evidence.scan_status, VerificationEvidence.ScanStatus.PENDING)
        with self.assertRaisesMessage(ValidationError, "pass malware scanning"):
            review_requirement(
                user=self.user,
                requirement=requirement,
                decision=VerificationCase.Status.UNDER_REVIEW,
            )

    def test_candidate_api_is_owner_scoped_and_uses_opaque_identifiers(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        other_person = make_person(
            company=self.company,
            email="other.verification.applicant@example.test",
        )
        client = Client()
        url = reverse(
            "tvg_careers_internal:verification-evidence-upload",
            args=(self.application.public_id, requirement.public_id),
        )
        settings = self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            CAREERHUB_VERIFICATION_API_ENABLED=True,
            CAREERHUB_VERIFICATION_API_TOKEN=VERIFICATION_TOKEN,
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        )
        with settings:
            denied = client.post(
                url,
                {
                    "person_public_id": str(other_person.public_id),
                    "document": SimpleUploadedFile(
                        "synthetic-proof.pdf", PDF, content_type="application/pdf"
                    ),
                },
                HTTP_AUTHORIZATION=f"Bearer {VERIFICATION_TOKEN}",
            )
            accepted = client.post(
                url,
                {
                    "person_public_id": str(self.person.public_id),
                    "document": SimpleUploadedFile(
                        "synthetic-proof.pdf", PDF, content_type="application/pdf"
                    ),
                },
                HTTP_AUTHORIZATION=f"Bearer {VERIFICATION_TOKEN}",
            )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(accepted.status_code, 201)
        payload = accepted.json()
        self.assertNotIn("candidate_id", accepted.content.decode())
        self.assertNotIn("person_public_id", accepted.content.decode())
        self.assertEqual(
            payload["verification"]["requirements"][0]["status"], "submitted"
        )

    def test_invalid_or_oversized_evidence_is_not_persisted(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        for upload in (
            SimpleUploadedFile(
                "synthetic-proof.txt", b"not evidence", content_type="text/plain"
            ),
            SimpleUploadedFile(
                "synthetic-proof.pdf",
                PDF + (b"x" * 1024 * 1024),
                content_type="application/pdf",
            ),
        ):
            with self.assertRaises(ValidationError):
                submit_evidence(
                    application=self.application,
                    requirement_public_id=requirement.public_id,
                    person_public_id=self.person.public_id,
                    upload=upload,
                )
        self.assertFalse(VerificationEvidence.all_objects.exists())

    def test_manager_permission_scope_and_cross_company_download(self):
        unprivileged, _employee = self._make_staff(
            "verification-unprivileged",
            company=self.company,
            manager=True,
            permissions=(),
        )
        with self.assertRaises(PermissionDenied):
            screen_application(
                user=unprivileged,
                application=self.application,
                action=ScreeningDecision.Status.HOLD,
            )

        not_assigned, _employee = self._make_staff(
            "verification-not-assigned",
            company=self.company,
            manager=False,
            permissions=("screen_application",),
        )
        with self.assertRaises(PermissionDenied):
            screen_application(
                user=not_assigned,
                application=self.application,
                action=ScreeningDecision.Status.HOLD,
            )

        case = self._shortlist()
        evidence = self._upload(case.requirements.first())
        other_company = make_company("Synthetic Other Employer")
        other_user, _other_employee = self._make_staff(
            "verification-other-company",
            company=other_company,
            manager=False,
            permissions=("review_verification",),
        )
        client = Client()
        client.force_login(other_user)
        with self.settings(ROOT_URLCONF="tvg_careers.peopleops_urls"):
            response = client.get(
                reverse("tvg_erasmus:evidence-download", args=(evidence.public_id,))
            )
        self.assertEqual(response.status_code, 403)

    def test_dashboard_scope_requires_view_permission_and_manager_assignment(self):
        self.assertIn(self.application, visible_applications(self.user))
        client = Client()
        client.force_login(self.user)
        with self.settings(ROOT_URLCONF="tvg_careers.peopleops_urls"):
            response = client.get(reverse("tvg_erasmus:screening-dashboard"))
        self.assertContains(response, self.application.opening.title)
        unprivileged, _employee = self._make_staff(
            "verification-dashboard-unprivileged",
            company=self.company,
            manager=True,
            permissions=(),
        )
        self.assertFalse(visible_applications(unprivileged).exists())

    def test_reminders_are_idempotent_events_and_never_send_mail(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        with patch("django.core.mail.send_mail") as send_mail:
            first = queue_requirement_reminder(
                user=self.user,
                requirement=requirement,
                now=timezone.now(),
            )
            second = queue_requirement_reminder(
                user=self.user,
                requirement=requirement,
                now=timezone.now(),
            )
        self.assertEqual(first.pk, second.pk)
        send_mail.assert_not_called()

    def test_retention_purge_deletes_bytes_but_keeps_minimal_metadata(self):
        case = self._shortlist()
        evidence = self._upload(case.requirements.first())
        path = evidence.file.path
        evidence.delete_after = timezone.now() - timedelta(seconds=1)
        evidence.save(update_fields=("delete_after",))

        purged = purge_expired_verification_evidence()
        evidence.refresh_from_db()
        self.assertEqual(purged, 1)
        self.assertIsNotNone(evidence.deleted_at)
        self.assertFalse(evidence.file.storage.exists(evidence.file.name))
        self.assertTrue(evidence.sha256)
        self.assertEqual(
            VerificationAuditEvent.all_objects.filter(case=case).count(),
            2,
        )

    def test_withdrawal_closes_verification_and_starts_retention(self):
        case = self._shortlist()
        evidence = self._upload(case.requirements.first())
        self.application.withdrawn_at = timezone.now()
        self.application.save(update_fields=("withdrawn_at",))
        close_verification_for_inactive_application(
            self.application,
            reason="withdrawn",
        )
        case.refresh_from_db()
        evidence.refresh_from_db()
        self.assertIsNotNone(case.completed_at)
        self.assertIsNotNone(evidence.delete_after)
        with self.assertRaises(PermissionDenied):
            self._upload(case.requirements.exclude(pk=evidence.requirement_id).first())

    def test_existing_application_has_compatible_not_requested_projection(self):
        self.assertFalse(
            VerificationCase.all_objects.filter(application=self.application).exists()
        )
        projection = verification_projection(self.application)
        self.assertEqual(projection["status"], VerificationCase.Status.NOT_REQUESTED)
        self.assertFalse(projection["requirements"])

    def test_projection_exposes_only_candidate_safe_fields(self):
        case = self._shortlist()
        requirement = case.requirements.first()
        requirement.internal_note = "Never expose this synthetic HR note."
        requirement.save(update_fields=("internal_note",))
        payload = _safe_projection(self.application)
        serialized = str(payload)
        self.assertNotIn("Never expose", serialized)
        self.assertNotIn("internal_note", serialized)
        self.assertNotIn("reviewed_by", serialized)
        self.assertIn("verification", payload)

    def test_rejected_screening_closes_uploads_and_starts_retention(self):
        case = self._shortlist()
        evidence = self._upload(case.requirements.first())
        decision = screen_application(
            user=self.user,
            application=self.application,
            action=ScreeningDecision.Status.REJECTED,
            applicant_message="Synthetic application was not selected.",
        )
        self.application.candidate.refresh_from_db()
        self.assertEqual(decision.status, ScreeningDecision.Status.REJECTED)
        self.assertTrue(self.application.candidate.canceled)
        evidence.refresh_from_db()
        self.assertIsNotNone(evidence.delete_after)
        with self.assertRaises(PermissionDenied):
            self._upload(case.requirements.exclude(pk=evidence.requirement_id).first())
        with self.assertRaisesMessage(ValidationError, "not accepting"):
            queue_requirement_reminder(
                user=self.user,
                requirement=case.requirements.first(),
            )

    def test_non_erasmus_shortlist_records_not_required(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Non-Erasmus Role",
            erasmus_plus_applicable=False,
        )
        application = make_application(opening=opening)
        opening.recruitment.recruitment_managers.add(self.employee)
        case = self._shortlist(application)
        self.assertEqual(case.status, VerificationCase.Status.NOT_REQUIRED)
        self.assertTrue(interview_eligibility(application).eligible)
