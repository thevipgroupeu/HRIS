from __future__ import annotations

import json
from datetime import date
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from horilla.testkit import make_company, make_employee, make_user
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture
from tvg_erasmus.models import (
    AcceptanceLetter,
    AcceptanceLetterTemplate,
    AcceptanceNotification,
    DurationException,
    ErasmusArrangementPolicy,
    ErasmusCase,
    VerificationCase,
    VerificationChecklist,
    VerificationRequirement,
    VerificationRequirementTemplate,
)
from tvg_erasmus.services import (
    approve_acceptance_template,
    approve_erasmus_case,
    arrangement_projection,
    confirm_acceptance_receipt,
    confirm_arrangement_dates,
    decide_duration_exception,
    issue_acceptance_letter,
    request_duration_exception,
    save_erasmus_case,
    screen_application,
    send_acceptance_letter,
)
from tvg_interviews.models import DecisionNotificationTemplate
from tvg_interviews.services import (
    book_interview,
    complete_interview,
    create_invitation,
    record_decision,
)
from tvg_interviews.tests.factories import future_slots, make_interview_type

ARRANGEMENT_TOKEN = "synthetic-arrangement-token-000000000000"


@override_settings(
    CAREERHUB_ARRANGEMENT_API_ENABLED=True,
    CAREERHUB_ARRANGEMENT_API_TOKEN=ARRANGEMENT_TOKEN,
    TVG_INTERVIEW_PROVIDER_BACKENDS={
        "simulated": "tvg_interviews.providers.SimulatedCalendarProvider",
        "zoho_bookings": "tvg_interviews.providers.DisabledCalendarProvider",
        "self_hosted": "tvg_interviews.providers.DisabledCalendarProvider",
    },
)
class AcceptanceWorkflowTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.addCleanup(self.private_media.cleanup)
        self.file_field = AcceptanceLetter._meta.get_field("pdf_file")
        self.original_storage = self.file_field.storage
        self.file_field.storage = FileSystemStorage(location=self.private_media.name)
        self.addCleanup(self._restore_storage)

        self.company = make_company("Synthetic Acceptance Employer")
        self.venture = make_venture(
            company=self.company,
            name="Synthetic Learning Studio",
            slug="synthetic-acceptance-learning-studio",
        )
        self.opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Erasmus Acceptance Role",
            is_published=True,
        )
        self.person = make_person(
            company=self.company,
            email="acceptance.candidate@example.test",
        )
        self.application = make_application(
            opening=self.opening,
            person=self.person,
            email=self.person.primary_email,
        )
        self.manager_user, self.manager = self._staff(
            "synthetic-acceptance-manager",
            company=self.company,
            permissions={
                "tvg_erasmus": {
                    "screen_application",
                    "manage_erasmus_case",
                    "approve_erasmus_case",
                    "request_duration_exception",
                    "approve_duration_exception",
                    "approve_acceptance_template",
                    "issue_acceptance_letter",
                    "send_acceptance_letter",
                },
                "tvg_interviews": {
                    "invite_candidate",
                    "manage_interview",
                    "record_decision",
                },
            },
        )
        self.opening.recruitment.recruitment_managers.add(self.manager)
        self.policy = ErasmusArrangementPolicy.all_objects.create(
            company=self.company,
            minimum_duration_months=3,
            letter_number_prefix="SYN-ACC",
        )
        self.decision = self._select_application(self.application)

    def _restore_storage(self):
        self.file_field.storage = self.original_storage

    def _staff(self, username, *, company, permissions):
        user = make_user(username, email=f"{username}@example.test")
        employee = make_employee(
            company=company,
            email=f"{username}.employee@example.test",
            user=user,
            first_name="Synthetic",
            last_name=username.replace("synthetic-", "").title(),
        )
        for app_label, codenames in permissions.items():
            grants = Permission.objects.filter(
                content_type__app_label=app_label,
                codename__in=codenames,
            )
            user.user_permissions.add(*grants)
        return type(user).objects.get(pk=user.pk), type(employee).objects.get(
            pk=employee.pk
        )

    def _verify_application(self, application):
        checklist = VerificationChecklist.all_objects.create(
            recruitment=application.opening.recruitment,
            retention_days=30,
        )
        rows = (
            ("identity", VerificationRequirementTemplate.Category.IDENTITY),
            (
                "student-status",
                VerificationRequirementTemplate.Category.STUDENT_STATUS,
            ),
            ("university", VerificationRequirementTemplate.Category.UNIVERSITY),
        )
        for sequence, (code, category) in enumerate(rows, start=1):
            VerificationRequirementTemplate.all_objects.create(
                checklist=checklist,
                code=code,
                category=category,
                title=f"Synthetic {code}",
                instructions="Use synthetic evidence only.",
                required=True,
                allowed_formats=["pdf"],
                max_size_mb=1,
                sequence=sequence,
            )
        screen_application(
            user=self.manager_user,
            application=application,
            action="shortlisted",
        )
        case = VerificationCase.all_objects.get(application=application)
        VerificationRequirement.all_objects.filter(case=case).update(
            status=VerificationCase.Status.VERIFIED,
            reviewed_at=timezone.now(),
            reviewed_by=self.manager,
        )
        case.status = VerificationCase.Status.VERIFIED
        case.reviewed_at = timezone.now()
        case.reviewed_by = self.manager
        case.completed_at = timezone.now()
        case.save()

    def _select_application(self, application):
        self._verify_application(application)
        interview_type = make_interview_type(
            opening=application.opening,
            interviewers=(self.manager,),
            code=f"synthetic-{uuid4().hex[:12]}",
        )
        invitation = create_invitation(
            user=self.manager_user,
            application=application,
            interview_type=interview_type,
            interviewers=(self.manager,),
            slot_starts=future_slots(48),
            candidate_timezone="Europe/Warsaw",
        )
        booking = book_interview(
            invitation_id=invitation.public_id,
            slot_id=invitation.slots.get().public_id,
            person_public_id=application.person.public_id,
            candidate_timezone="Europe/Warsaw",
        )
        complete_interview(user=self.manager_user, booking=booking)
        return record_decision(
            user=self.manager_user,
            booking=booking,
            outcome=DecisionNotificationTemplate.Outcome.SELECTED,
            internal_reason="Synthetic human selection for acceptance testing.",
        )

    def _case_values(self, **overrides):
        values = {
            "university": "Synthetic University",
            "programme": "Synthetic International Business MSc",
            "funding_declaration": ErasmusCase.FundingDeclaration.ELIGIBLE_FUNDED,
            "proposed_start_date": date(2027, 1, 15),
            "proposed_end_date": date(2027, 4, 15),
            "working_model": "hybrid",
            "work_location": "Synthetic Warsaw Office",
            "department": self.opening.job_position.department_id,
            "supervisor": self.manager,
            "curriculum_reference": "SYN-CURRICULUM-01",
            "curriculum_version": "2027.1",
            "university_confirmation_status": (
                ErasmusCase.UniversityConfirmation.CONFIRMED
            ),
        }
        values.update(overrides)
        return values

    def _create_case(self, **overrides):
        return save_erasmus_case(
            user=self.manager_user,
            application=self.application,
            selection_decision=self.decision,
            **self._case_values(**overrides),
        )

    def _approved_case(self):
        case = self._create_case()
        confirm_arrangement_dates(
            case_id=case.public_id,
            person_public_id=self.person.public_id,
            start_date=date(2027, 1, 15),
            end_date=date(2027, 4, 15),
        )
        return approve_erasmus_case(user=self.manager_user, case=case)

    def _template(self, *, version=1, approved=True):
        template = AcceptanceLetterTemplate.all_objects.create(
            company=self.company,
            name="Synthetic company-authored acceptance",
            version_number=version,
            body=(
                "Dear {candidate_name},\n\n"
                "This company-authored letter confirms the approved {role_title} "
                "internship arrangements from {confirmed_start_date} to "
                "{confirmed_end_date} under curriculum {curriculum_reference} "
                "version {curriculum_version}.\n\n"
                "Working arrangement: {working_model}, {work_location}."
                " Department: {department}. Supervisor: {supervisor}."
            ),
            legal_entity_name="Synthetic Legal Entity sp. z o.o.",
            legal_address="1 Synthetic Test Street, 00-001 Warsaw",
            registration_details="Synthetic registry TEST-000001",
            authorized_signatory_name="Synthetic Authorized Signatory",
            authorized_signatory_title="Synthetic People Director",
        )
        if approved:
            template = approve_acceptance_template(
                user=self.manager_user,
                template=template,
            )
        return template

    def test_candidate_completes_acceptance_workflow_through_private_api(self):
        case = self._create_case()
        client = Client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {ARRANGEMENT_TOKEN}"}
        settings = self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        )
        with settings:
            confirmation = client.post(
                reverse(
                    "tvg_careers_internal:arrangement-confirm-dates",
                    args=(case.public_id,),
                ),
                data=json.dumps(
                    {
                        "person_public_id": str(self.person.public_id),
                        "start_date": "2027-01-15",
                        "end_date": "2027-04-15",
                    }
                ),
                content_type="application/json",
                **headers,
            )
        self.assertEqual(confirmation.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(
            case.arrangement_status,
            ErasmusCase.ArrangementStatus.CANDIDATE_CONFIRMED,
        )
        self.assertEqual(case.internship_duration_days, 91)

        approve_erasmus_case(user=self.manager_user, case=case)
        template = self._template()
        letter = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=template,
            issue_date=date(2026, 10, 26),
        )
        with patch("django.core.mail.send_mail") as send_mail:
            send_acceptance_letter(user=self.manager_user, letter=letter)
        send_mail.assert_not_called()
        self.assertTrue(
            AcceptanceNotification.all_objects.filter(
                letter=letter,
                published_at__isnull=True,
            ).exists()
        )

        with settings:
            download = client.get(
                reverse(
                    "tvg_careers_internal:acceptance-download",
                    args=(case.public_id, letter.public_id),
                ),
                {"person_public_id": str(self.person.public_id)},
                **headers,
            )
            receipt = client.post(
                reverse(
                    "tvg_careers_internal:acceptance-confirm-receipt",
                    args=(case.public_id, letter.public_id),
                ),
                data=json.dumps({"person_public_id": str(self.person.public_id)}),
                content_type="application/json",
                **headers,
            )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")
        with patch("django.http.response.signals.request_finished.send"):
            download.close()
        self.assertEqual(receipt.status_code, 200)
        letter.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(letter.status, AcceptanceLetter.Status.RECEIVED)
        self.assertEqual(
            case.acceptance_status,
            ErasmusCase.AcceptanceStatus.RECEIVED,
        )
        self.assertTrue(
            arrangement_projection(self.application)["learning_agreement_ready"]
        )

    def test_candidate_date_conflict_is_rejected(self):
        first = self._create_case()
        confirm_arrangement_dates(
            case_id=first.public_id,
            person_public_id=self.person.public_id,
            start_date=date(2027, 1, 15),
            end_date=date(2027, 4, 15),
        )

        other_opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Second Synthetic Erasmus Role",
        )
        other_opening.recruitment.recruitment_managers.add(self.manager)
        other_application = make_application(
            opening=other_opening,
            person=self.person,
            email=self.person.primary_email,
        )
        other_decision = self._select_application(other_application)
        second = save_erasmus_case(
            user=self.manager_user,
            application=other_application,
            selection_decision=other_decision,
            **{
                **self._case_values(),
                "department": other_opening.job_position.department_id,
            },
        )
        with self.assertRaisesMessage(ValidationError, "overlap"):
            confirm_arrangement_dates(
                case_id=second.public_id,
                person_public_id=self.person.public_id,
                start_date=date(2027, 4, 1),
                end_date=date(2027, 7, 1),
            )
        second.refresh_from_db()
        self.assertIsNone(second.confirmed_start_date)

    def test_short_duration_requires_independent_authorized_exception(self):
        case = self._create_case(
            proposed_start_date=date(2027, 1, 15),
            proposed_end_date=date(2027, 3, 14),
        )
        confirm_arrangement_dates(
            case_id=case.public_id,
            person_public_id=self.person.public_id,
            start_date=date(2027, 1, 15),
            end_date=date(2027, 3, 14),
        )
        with self.assertRaisesMessage(ValidationError, "still outstanding"):
            approve_erasmus_case(user=self.manager_user, case=case)

        exception = request_duration_exception(
            user=self.manager_user,
            case=case,
            reason="Synthetic university term ends before the normal minimum.",
        )
        with self.assertRaisesMessage(ValidationError, "pending duration exception"):
            confirm_arrangement_dates(
                case_id=case.public_id,
                person_public_id=self.person.public_id,
                start_date=date(2027, 1, 15),
                end_date=date(2027, 3, 31),
            )
        with self.assertRaisesMessage(ValidationError, "pending duration exception"):
            save_erasmus_case(
                user=self.manager_user,
                application=self.application,
                selection_decision=self.decision,
                case=case,
                **self._case_values(
                    proposed_start_date=date(2027, 1, 15),
                    proposed_end_date=date(2027, 3, 31),
                ),
            )
        with self.assertRaisesMessage(ValidationError, "own exception"):
            decide_duration_exception(
                user=self.manager_user,
                exception=exception,
                decision=DurationException.Status.APPROVED,
            )

        approver_user, approver = self._staff(
            "synthetic-exception-approver",
            company=self.company,
            permissions={
                "tvg_erasmus": {"approve_duration_exception"},
            },
        )
        self.opening.recruitment.recruitment_managers.add(approver)
        decide_duration_exception(
            user=approver_user,
            exception=exception,
            decision=DurationException.Status.APPROVED,
            note="Synthetic exception reviewed against the university calendar.",
        )
        approve_erasmus_case(user=self.manager_user, case=case)
        case.refresh_from_db()
        self.assertEqual(
            case.approval_snapshot["duration_exception_version"],
            exception.version_number,
        )
        self.assertEqual(case.confirmed_end_date, date(2027, 3, 14))

    def test_arrangement_and_template_approval_gate_issuance(self):
        case = self._create_case(
            university_confirmation_status=ErasmusCase.UniversityConfirmation.PENDING
        )
        confirm_arrangement_dates(
            case_id=case.public_id,
            person_public_id=self.person.public_id,
            start_date=date(2027, 1, 15),
            end_date=date(2027, 4, 15),
        )
        template = self._template(approved=False)
        with self.assertRaisesMessage(ValidationError, "HR-approved"):
            issue_acceptance_letter(
                user=self.manager_user,
                case=case,
                template=template,
            )
        with self.assertRaisesMessage(ValidationError, "still outstanding"):
            approve_erasmus_case(user=self.manager_user, case=case)

        case = save_erasmus_case(
            user=self.manager_user,
            application=self.application,
            selection_decision=self.decision,
            case=case,
            **self._case_values(),
        )
        approve_erasmus_case(user=self.manager_user, case=case)
        with self.assertRaisesMessage(ValidationError, "approved legal-employer"):
            issue_acceptance_letter(
                user=self.manager_user,
                case=case,
                template=template,
            )

        other_company = make_company("Synthetic Unauthorized Employer")
        outsider_user, _outsider = self._staff(
            "synthetic-acceptance-outsider",
            company=other_company,
            permissions={"tvg_erasmus": {"issue_acceptance_letter"}},
        )
        template = approve_acceptance_template(
            user=self.manager_user,
            template=template,
        )
        with self.assertRaises(PermissionDenied):
            issue_acceptance_letter(
                user=outsider_user,
                case=case,
                template=template,
            )
        self.assertFalse(AcceptanceLetter.all_objects.exists())

    def test_document_versions_are_immutable_and_only_latest_can_be_sent(self):
        case = self._approved_case()
        approved_candidate_name = case.approval_snapshot["candidate_name"]
        approved_role_title = case.approval_snapshot["role_title"]
        approved_department = case.approval_snapshot["department"]
        approved_supervisor = case.approval_snapshot["supervisor"]

        candidate = case.candidate
        candidate.name = "Changed After Approval Candidate"
        candidate.save()
        recruitment = case.application.opening.recruitment
        recruitment.title = "Changed After Approval Role"
        recruitment.save(update_fields=("title",))
        department = case.department
        department.department = "Changed After Approval Department"
        department.save(update_fields=("department",))
        supervisor = case.supervisor
        supervisor.employee_first_name = "Changed"
        supervisor.employee_last_name = "After Approval"
        supervisor.save()

        first_template = self._template(version=1)
        first = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=first_template,
            issue_date=date(2026, 10, 26),
        )
        self.assertIn(approved_candidate_name, first.rendered_text)
        self.assertIn(approved_role_title, first.rendered_text)
        self.assertIn(approved_department, first.rendered_text)
        self.assertIn(approved_supervisor, first.rendered_text)
        self.assertNotIn("Changed After Approval", first.rendered_text)
        send_acceptance_letter(user=self.manager_user, letter=first)

        second_template = self._template(version=2)
        second = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=second_template,
            issue_date=date(2026, 10, 27),
        )

        self.assertEqual(first.document_version, 1)
        self.assertEqual(second.document_version, 2)
        self.assertNotEqual(first.letter_number, second.letter_number)
        self.assertEqual(AcceptanceLetter.all_objects.filter(case=case).count(), 2)
        self.assertTrue(first.pdf_file.storage.exists(first.pdf_file.name))
        self.assertTrue(second.pdf_file.storage.exists(second.pdf_file.name))
        first_projection = next(
            item
            for item in arrangement_projection(self.application)["letters"]
            if item["id"] == str(first.public_id)
        )
        self.assertFalse(first_projection["can_confirm_receipt"])
        with self.assertRaisesMessage(ValidationError, "latest document version"):
            confirm_acceptance_receipt(
                case_id=case.public_id,
                letter_id=first.public_id,
                person_public_id=self.person.public_id,
            )

        first.rendered_text = "Attempted synthetic mutation"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            first.save()
        first.refresh_from_db()
        send_acceptance_letter(user=self.manager_user, letter=second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, AcceptanceLetter.Status.SENT)
        self.assertEqual(second.status, AcceptanceLetter.Status.SENT)

    def test_candidate_api_hides_cases_from_other_people(self):
        case = self._create_case()
        other_person = make_person(
            company=self.company,
            email="other.acceptance.candidate@example.test",
        )
        client = Client()
        with self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        ):
            response = client.post(
                reverse(
                    "tvg_careers_internal:arrangement-confirm-dates",
                    args=(case.public_id,),
                ),
                data=json.dumps(
                    {
                        "person_public_id": str(other_person.public_id),
                        "start_date": "2027-01-15",
                        "end_date": "2027-04-15",
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {ARRANGEMENT_TOKEN}",
            )
        self.assertEqual(response.status_code, 404)
        case.refresh_from_db()
        self.assertIsNone(case.candidate_confirmed_at)
