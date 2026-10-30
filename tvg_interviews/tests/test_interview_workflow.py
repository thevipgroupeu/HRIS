from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from horilla.testkit import make_company, make_employee, make_user
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture
from tvg_erasmus.models import (
    ScreeningDecision,
    VerificationCase,
    VerificationChecklist,
    VerificationRequirement,
    VerificationRequirementTemplate,
)
from tvg_erasmus.services import screen_application
from tvg_interviews.models import (
    DecisionNotificationTemplate,
    InterviewBooking,
    InterviewDecision,
    InterviewFeedback,
    InterviewInvitation,
    InterviewNotification,
)
from tvg_interviews.services import (
    book_interview,
    cancel_interview,
    complete_interview,
    create_invitation,
    interview_projection,
    queue_interview_reminder,
    record_decision,
    record_feedback,
    reschedule_interview,
    visible_invitations,
)
from tvg_interviews.tests.factories import future_slots, make_interview_type

INTERVIEW_TOKEN = "synthetic-interview-token-000000000000000"


@override_settings(
    CAREERHUB_INTERVIEW_API_TOKEN=INTERVIEW_TOKEN,
    CAREERHUB_INTERVIEW_API_ENABLED=True,
    TVG_INTERVIEW_PROVIDER_BACKENDS={
        "simulated": "tvg_interviews.providers.SimulatedCalendarProvider",
        "zoho_bookings": "tvg_interviews.providers.DisabledCalendarProvider",
        "self_hosted": "tvg_interviews.providers.DisabledCalendarProvider",
    },
)
class InterviewWorkflowTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Interview Employer")
        self.venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="synthetic-interview-fluxops",
        )
        self.opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Verified Interview Role",
            is_published=True,
        )
        self.application = make_application(
            opening=self.opening,
            person=make_person(
                company=self.company,
                email="interview.candidate@example.test",
            ),
        )
        self.manager_user, self.manager = self._staff(
            "synthetic-interview-manager",
            manager=True,
            permissions={
                "tvg_erasmus": {
                    "view_screeningdecision",
                    "screen_application",
                },
                "tvg_interviews": {
                    "view_interviewinvitation",
                    "invite_candidate",
                    "manage_interview",
                    "record_feedback",
                    "record_decision",
                },
            },
        )
        self.interviewer_user, self.interviewer = self._staff(
            "synthetic-interviewer",
            permissions={
                "tvg_interviews": {
                    "view_interviewinvitation",
                    "record_feedback",
                }
            },
        )
        self.interview_type = make_interview_type(
            opening=self.opening,
            interviewers=(self.interviewer,),
        )

    def _staff(self, username, *, manager=False, company=None, permissions=None):
        company = company or self.company
        user = make_user(username, email=f"{username}@example.test")
        employee = make_employee(
            company=company,
            email=f"{username}.employee@example.test",
            user=user,
        )
        if manager:
            self.opening.recruitment.recruitment_managers.add(employee)
        for app_label, codenames in (permissions or {}).items():
            grants = Permission.objects.filter(
                content_type__app_label=app_label,
                codename__in=codenames,
            )
            user.user_permissions.add(*grants)
        return type(user).objects.get(pk=user.pk), type(employee).objects.get(
            pk=employee.pk
        )

    def _checklist(self):
        checklist, _created = VerificationChecklist.all_objects.get_or_create(
            recruitment=self.opening.recruitment,
            defaults={"retention_days": 30},
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
            VerificationRequirementTemplate.all_objects.get_or_create(
                checklist=checklist,
                code=code,
                defaults={
                    "category": category,
                    "title": f"Synthetic {code}",
                    "instructions": "Use synthetic evidence only.",
                    "required": True,
                    "allowed_formats": ["pdf"],
                    "max_size_mb": 1,
                    "sequence": sequence,
                },
            )
        return checklist

    def _verify(self, application=None):
        application = application or self.application
        self._checklist()
        screen_application(
            user=self.manager_user,
            application=application,
            action=ScreeningDecision.Status.SHORTLISTED,
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
        return case

    def _invite(self, application=None, slots=None, interviewers=None):
        application = application or self.application
        return create_invitation(
            user=self.manager_user,
            application=application,
            interview_type=self.interview_type,
            interviewers=interviewers or (self.interviewer,),
            slot_starts=slots or future_slots(48, 72, 96),
            candidate_timezone="Europe/Warsaw",
        )

    def _book(self, invitation, *, slot_index=0, timezone_name="Europe/Tallinn"):
        slot = list(invitation.slots.order_by("start_at"))[slot_index]
        return book_interview(
            invitation_id=invitation.public_id,
            slot_id=slot.public_id,
            person_public_id=invitation.application.person.public_id,
            candidate_timezone=timezone_name,
        )

    def test_verification_gate_blocks_external_invitation(self):
        with self.assertRaisesMessage(ValidationError, "invitation blocked"):
            self._invite()
        self.assertFalse(InterviewInvitation.all_objects.exists())

    def test_verified_candidate_books_timezone_safe_native_schedule(self):
        self._verify()
        invitation = self._invite()
        booking = self._book(invitation)

        expected = timezone.localtime(
            booking.slot.start_at,
            ZoneInfo("Europe/Warsaw"),
        )
        self.assertEqual(booking.native_schedule.interview_date, expected.date())
        self.assertEqual(
            booking.native_schedule.interview_time.replace(microsecond=0),
            expected.time().replace(tzinfo=None, microsecond=0),
        )
        self.assertEqual(
            list(booking.native_schedule.employee_id.all()), [self.interviewer]
        )
        self.application.candidate.refresh_from_db()
        self.assertEqual(self.application.candidate.stage_id.stage_type, "interview")

        projection = interview_projection(
            self.application,
            candidate_timezone="Europe/Tallinn",
        )[0]
        self.assertEqual(projection["company_timezone"], "Europe/Warsaw")
        self.assertEqual(projection["booking"]["candidate_timezone"], "Europe/Tallinn")
        self.assertNotEqual(
            projection["booking"]["candidate_display"],
            projection["booking"]["company_display"],
        )
        self.assertNotIn("feedback", projection)

    def test_conflicting_interviewer_booking_is_rejected(self):
        self._verify()
        shared_start = future_slots(48)[0]
        first = self._invite(slots=(shared_start,))
        self._book(first)

        second_application = make_application(
            opening=self.opening,
            person=make_person(
                company=self.company,
                email="second.interview.candidate@example.test",
            ),
        )
        self._verify(second_application)
        second = self._invite(second_application, slots=(shared_start,))
        with self.assertRaisesMessage(ValidationError, "conflicts"):
            self._book(second)
        self.assertFalse(
            InterviewBooking.all_objects.filter(invitation=second).exists()
        )

    def test_staff_object_permissions_and_cross_company_scope(self):
        self._verify()
        invitation = self._invite()
        other_company = make_company("Synthetic Other Employer")
        outsider_user, _outsider = self._staff(
            "synthetic-outsider",
            company=other_company,
            permissions={
                "tvg_interviews": {
                    "view_interviewinvitation",
                    "invite_candidate",
                }
            },
        )
        self.assertFalse(visible_invitations(outsider_user).exists())
        with self.assertRaises(PermissionDenied):
            create_invitation(
                user=outsider_user,
                application=self.application,
                interview_type=self.interview_type,
                interviewers=(self.interviewer,),
                slot_starts=future_slots(120),
            )
        self.assertTrue(
            visible_invitations(self.interviewer_user).filter(pk=invitation.pk).exists()
        )

    def test_one_candidate_reschedule_and_twelve_hour_notice(self):
        self._verify()
        invitation = self._invite()
        booking = self._book(invitation)
        replacement = invitation.slots.filter(status="available").first()
        booking = reschedule_interview(
            invitation_id=invitation.public_id,
            slot_id=replacement.public_id,
            person_public_id=self.application.person.public_id,
            candidate_timezone="Europe/Paris",
        )
        self.assertEqual(booking.candidate_reschedule_count, 1)
        with self.assertRaisesMessage(ValidationError, "allowance"):
            reschedule_interview(
                invitation_id=invitation.public_id,
                slot_id=invitation.slots.filter(status="available").first().public_id,
                person_public_id=self.application.person.public_id,
                candidate_timezone="Europe/Paris",
            )

        second_application = make_application(
            opening=self.opening,
            person=make_person(
                company=self.company,
                email="notice.interview.candidate@example.test",
            ),
        )
        self._verify(second_application)
        short_notice = self._invite(
            second_application,
            slots=future_slots(10, 30),
        )
        short_booking = self._book(short_notice)
        target = short_notice.slots.filter(status="available").first()
        with self.assertRaisesMessage(ValidationError, "12 hours"):
            reschedule_interview(
                invitation_id=short_notice.public_id,
                slot_id=target.public_id,
                person_public_id=second_application.person.public_id,
            )
        with self.assertRaises(PermissionDenied):
            reschedule_interview(
                user=self.manager_user,
                invitation_id=short_notice.public_id,
                slot_id=target.public_id,
                override_notice=True,
            )
        override = Permission.objects.get(
            content_type__app_label="tvg_interviews",
            codename="override_reschedule_policy",
        )
        self.manager_user.user_permissions.add(override)
        self.manager_user = type(self.manager_user).objects.get(pk=self.manager_user.pk)
        rescheduled = reschedule_interview(
            user=self.manager_user,
            invitation_id=short_notice.public_id,
            slot_id=target.public_id,
            override_notice=True,
        )
        self.assertEqual(rescheduled.pk, short_booking.pk)
        self.assertEqual(rescheduled.candidate_reschedule_count, 0)

    def test_private_feedback_and_approved_human_rejection(self):
        self._verify()
        invitation = self._invite()
        booking = self._book(invitation)
        complete_interview(user=self.manager_user, booking=booking)

        observer_user, _observer = self._staff(
            "synthetic-feedback-observer",
            permissions={"tvg_interviews": {"record_feedback"}},
        )
        with self.assertRaises(PermissionDenied):
            record_feedback(
                user=observer_user,
                booking=booking,
                competency_ratings={"Communication": 3},
            )

        feedback = record_feedback(
            user=self.interviewer_user,
            booking=booking,
            competency_ratings={"Communication": 4, "Technical": 3},
            strengths="Synthetic strength.",
            private_notes="Never candidate-visible.",
            recommendation="hold",
        )
        self.assertEqual(feedback.interviewer, self.interviewer)
        self.assertEqual(InterviewFeedback.all_objects.count(), 1)
        with self.assertRaisesMessage(ValidationError, "already"):
            record_feedback(
                user=self.interviewer_user,
                booking=booking,
                competency_ratings={"Communication": 5},
            )

        limited_user, _limited_employee = self._staff(
            "synthetic-feedback-limited-manager",
            manager=True,
            permissions={
                "tvg_interviews": {"view_interviewinvitation"},
            },
        )
        staff_client = Client()
        staff_client.force_login(limited_user)
        limited_dashboard = staff_client.get(reverse("tvg_interviews:dashboard"))
        self.assertEqual(limited_dashboard.status_code, 200)
        self.assertNotContains(limited_dashboard, "Never candidate-visible.")

        unapproved = DecisionNotificationTemplate.all_objects.create(
            recruitment=self.opening.recruitment,
            name="Synthetic rejection pending approval",
            outcome=DecisionNotificationTemplate.Outcome.REJECTED,
            subject="Update on {role_title}",
            body="Dear {candidate_name}, the human panel will not progress your {venture_name} application.",
        )
        with self.assertRaisesMessage(ValidationError, "approved"):
            record_decision(
                user=self.manager_user,
                booking=booking,
                outcome=DecisionNotificationTemplate.Outcome.REJECTED,
                template=unapproved,
                internal_reason="Synthetic human rationale.",
            )
        unapproved.approved = True
        unapproved.approved_by = self.manager
        unapproved.approved_at = timezone.now()
        unapproved.save()
        unapproved.subject = "Changed after approval"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            unapproved.save()
        unapproved.refresh_from_db()
        decision = record_decision(
            user=self.manager_user,
            booking=booking,
            outcome=DecisionNotificationTemplate.Outcome.REJECTED,
            template=unapproved,
            internal_reason="Synthetic human rationale.",
        )
        self.assertIsInstance(decision, InterviewDecision)
        self.assertEqual(decision.decided_by, self.manager)
        self.application.candidate.refresh_from_db()
        self.assertTrue(self.application.candidate.canceled)
        self.assertEqual(self.application.candidate.stage_id.stage_type, "cancelled")
        candidate_projection = interview_projection(self.application)[0]
        self.assertNotIn("private_notes", json.dumps(candidate_projection))
        self.assertNotIn("Synthetic human rationale", json.dumps(candidate_projection))

    def test_selected_and_hold_decisions_feed_configured_horilla_stages(self):
        for index, (outcome, expected_stage) in enumerate(
            (
                (DecisionNotificationTemplate.Outcome.SELECTED, "hired"),
                (DecisionNotificationTemplate.Outcome.HOLD, "test"),
            )
        ):
            with self.subTest(outcome=outcome):
                application = make_application(
                    opening=self.opening,
                    person=make_person(
                        company=self.company,
                        email=f"{outcome}.candidate@example.test",
                    ),
                )
                self._verify(application)
                invitation = self._invite(
                    application,
                    slots=future_slots(120 + index * 24),
                )
                booking = self._book(invitation)
                complete_interview(user=self.manager_user, booking=booking)
                decision = record_decision(
                    user=self.manager_user,
                    booking=booking,
                    outcome=outcome,
                    internal_reason="Synthetic human panel decision.",
                )
                application.candidate.refresh_from_db()
                self.assertEqual(
                    application.candidate.stage_id.stage_type, expected_stage
                )
                self.assertEqual(decision.resulting_stage.stage_type, expected_stage)

    def test_notification_outbox_covers_reminder_and_cancellation(self):
        self._verify()
        invitation = self._invite()
        booking = self._book(invitation)
        queue_interview_reminder(booking=booking)
        cancel_interview(
            user=self.manager_user,
            booking=booking,
            reason="Synthetic scheduling change.",
        )
        event_types = set(
            InterviewNotification.all_objects.filter(invitation=invitation).values_list(
                "event_type", flat=True
            )
        )
        self.assertTrue(
            {
                InterviewNotification.EventType.INVITATION,
                InterviewNotification.EventType.BOOKING_CONFIRMATION,
                InterviewNotification.EventType.REMINDER,
                InterviewNotification.EventType.CANCELLATION,
            }.issubset(event_types)
        )
        self.assertTrue(
            InterviewNotification.all_objects.filter(
                invitation=invitation,
                event_type=InterviewNotification.EventType.BOOKING_CONFIRMATION,
                audience=InterviewNotification.Audience.INTERVIEWER,
                recipient_employee=self.interviewer,
            ).exists()
        )
        self.assertTrue(
            InterviewNotification.all_objects.filter(
                invitation=invitation,
                event_type=InterviewNotification.EventType.BOOKING_CONFIRMATION,
                audience=InterviewNotification.Audience.HR,
                recipient_employee=self.manager,
            ).exists()
        )

    def test_internal_api_enforces_token_and_candidate_ownership(self):
        self._verify()
        invitation = self._invite()
        slot = invitation.slots.first()
        url = f"/internal/careerhub/v1/interviews/{invitation.public_id}/book/"
        payload = {
            "person_public_id": str(uuid4()),
            "slot_id": str(slot.public_id),
            "candidate_timezone": "Europe/Paris",
        }
        client = Client()
        self.assertEqual(
            client.post(
                url, data=json.dumps(payload), content_type="application/json"
            ).status_code,
            404,
        )
        headers = {"HTTP_AUTHORIZATION": f"Bearer {INTERVIEW_TOKEN}"}
        self.assertEqual(
            client.post(
                url,
                data=json.dumps(payload),
                content_type="application/json",
                **headers,
            ).status_code,
            404,
        )
        payload["person_public_id"] = str(self.application.person.public_id)
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            InterviewBooking.all_objects.filter(invitation=invitation).exists()
        )
