from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from horilla.testkit import make_company, make_employee, make_user
from tvg_automation.checks import automation_configuration_check
from tvg_automation.events import (
    APPLICATION_ACKNOWLEDGMENT,
    DOCUMENT_REMINDER,
    VERIFICATION_REQUEST,
)
from tvg_automation.models import (
    AutomationMessageTemplate,
    DeliveryAttempt,
    LifecycleEvent,
    WebhookReceipt,
    WorkflowExecution,
)
from tvg_automation.services import (
    approve_template,
    cancel_event,
    dispatch_due_events,
    enqueue_lifecycle_event,
    replay_event,
)
from tvg_automation.signing import body_signature, signature, verify_signature
from tvg_automation.transport import SyntheticActivepiecesTransport
from tvg_careers.models import ApplicationConfirmationEvent
from tvg_careers.questionnaires import enqueue_application_confirmation
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture
from tvg_erasmus.models import (
    ScreeningDecision,
    VerificationCase,
    VerificationChecklist,
    VerificationRequirementTemplate,
)
from tvg_erasmus.services import queue_requirement_reminder, screen_application


@override_settings(
    ROOT_URLCONF="tvg_careers.peopleops_urls",
    TVG_AUTOMATION_ENABLED=True,
    TVG_AUTOMATION_ENVIRONMENT="test",
    TVG_AUTOMATION_TRANSPORT_BACKEND=(
        "tvg_automation.transport.SyntheticActivepiecesTransport"
    ),
    TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET=(
        "synthetic-outbound-signing-secret-000000000000"
    ),
    TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET=(
        "synthetic-callback-signing-secret-000000000000"
    ),
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class WorkflowAutomationTests(TestCase):
    def setUp(self):
        SyntheticActivepiecesTransport.reset()
        self.company = make_company("Synthetic Automation Employer")
        self.venture = make_venture(
            company=self.company,
            name="Synthetic Automation Venture",
            slug="synthetic-automation-venture",
        )
        self.opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            is_published=True,
            title="Synthetic Automation Internship",
        )
        self.person = make_person(
            company=self.company,
            email="automation.candidate@example.test",
            preferred_name="Synthetic Automation Candidate",
        )
        self.application = make_application(
            opening=self.opening,
            person=self.person,
            email=self.person.primary_email,
        )
        self.user = make_user(
            "synthetic-automation-manager",
            email="automation.manager@example.test",
        )
        self.employee = make_employee(
            company=self.company,
            email="automation.manager.employee@example.test",
            user=self.user,
        )
        self.opening.recruitment.recruitment_managers.add(self.employee)
        permissions = Permission.objects.filter(
            content_type__app_label__in=("tvg_erasmus", "tvg_automation"),
            codename__in=(
                "screen_application",
                "manage_verification",
                "send_verification_reminder",
                "approve_message_template",
                "authorize_lifecycle_event",
                "cancel_lifecycle_event",
                "replay_lifecycle_event",
                "review_automation_failure",
            ),
        )
        self.user.user_permissions.add(*permissions)
        self.user = type(self.user).objects.get(pk=self.user.pk)
        self.employee = type(self.employee).objects.get(pk=self.employee.pk)
        self._checklist()
        self._approve_templates()

    def tearDown(self):
        SyntheticActivepiecesTransport.reset()

    def _checklist(self):
        checklist = VerificationChecklist.all_objects.create(
            recruitment=self.opening.recruitment,
            retention_days=30,
        )
        for sequence, (code, category) in enumerate(
            (
                ("identity", VerificationRequirementTemplate.Category.IDENTITY),
                (
                    "student-status",
                    VerificationRequirementTemplate.Category.STUDENT_STATUS,
                ),
                ("university", VerificationRequirementTemplate.Category.UNIVERSITY),
            ),
            start=1,
        ):
            VerificationRequirementTemplate.all_objects.create(
                checklist=checklist,
                code=code,
                category=category,
                title=f"Synthetic {code}",
                instructions="Upload a synthetic document.",
                required=True,
                allowed_formats=["pdf"],
                max_size_mb=1,
                sequence=sequence,
            )

    def _approve_templates(self):
        values = {
            "application_acknowledgment": (
                "Application received: {job_title}",
                "Hello {candidate_name}. We received application {application_id} on {submitted_at}.",
            ),
            "shortlisted_verification_request": (
                "Verification documents requested",
                "Hello {candidate_name}. {candidate_message} {next_action} Due {due_date}.",
            ),
            "document_reminder": (
                "Document reminder: {document_title}",
                "Hello {candidate_name}. {candidate_message} {next_action}",
            ),
        }
        for workflow_key, (subject, body) in values.items():
            template = AutomationMessageTemplate.all_objects.create(
                company=self.company,
                workflow_key=workflow_key,
                version=1,
                subject_template=subject,
                body_template=body,
            )
            approve_template(user=self.user, template=template)

    def _create_initial_three(self):
        enqueue_application_confirmation(self.application)
        screen_application(
            user=self.user,
            application=self.application,
            action=ScreeningDecision.Status.SHORTLISTED,
        )
        case = VerificationCase.all_objects.get(application=self.application)
        now = timezone.now()
        reminder = queue_requirement_reminder(
            user=self.user,
            requirement=case.requirements.first(),
            now=now,
        )
        return case, reminder, now

    def _signed_callback(
        self, event, *, status, key, execution_id=None, message="", extra=None
    ):
        execution_id = (
            execution_id or event.workflow_executions.first().external_execution_id
        )
        payload = {
            "event_id": str(event.event_id),
            "workflow_key": event.workflow_key,
            "execution_id": execution_id,
            "status": status,
            "message": message,
        }
        payload.update(extra or {})
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        timestamp = str(int(timezone.now().timestamp()))
        secret = "synthetic-callback-signing-secret-000000000000"
        return Client().post(
            "/internal/automation/v1/executions/",
            data=body,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            HTTP_X_TVG_TIMESTAMP=timestamp,
            HTTP_X_TVG_SIGNATURE=signature(secret, timestamp, body),
        )

    def test_initial_three_automations_dispatch_signed_approved_messages(self):
        case, _reminder, _now = self._create_initial_three()
        events = LifecycleEvent.all_objects.order_by("occurred_at", "pk")
        self.assertEqual(events.count(), 3)
        self.assertEqual(
            set(events.values_list("event_type", flat=True)),
            {APPLICATION_ACKNOWLEDGMENT, VERIFICATION_REQUEST, DOCUMENT_REMINDER},
        )
        verification = events.get(event_type=VERIFICATION_REQUEST)
        self.assertTrue(verification.human_approval_required)
        self.assertEqual(verification.approved_by, self.employee)
        self.assertEqual(verification.aggregate_id, case.public_id)

        self.assertEqual(dispatch_due_events(), 3)
        self.assertEqual(len(SyntheticActivepiecesTransport.deliveries), 3)
        self.assertEqual(len(mail.outbox), 0)
        for delivered in SyntheticActivepiecesTransport.deliveries:
            payload = delivered["payload"]
            headers = delivered["headers"]
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["message"]["from"], "recruitment@thevipgroup.eu")
            self.assertEqual(
                payload["message"]["reply_to"], "recruitment@thevipgroup.eu"
            )
            self.assertEqual(
                payload["message"]["to"]["email"], self.person.primary_email
            )
            self.assertEqual(headers["Idempotency-Key"], payload["idempotency_key"])
            body = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.assertTrue(
                verify_signature(
                    "synthetic-outbound-signing-secret-000000000000",
                    headers["X-TVG-Timestamp"],
                    body,
                    headers["X-TVG-Signature"],
                )
            )
            self.assertEqual(
                headers["X-TVG-Body-Signature"],
                body_signature("synthetic-outbound-signing-secret-000000000000", body),
            )
        self.assertFalse(
            "email"
            in json.dumps(
                events.get(event_type=APPLICATION_ACKNOWLEDGMENT).payload
            ).lower()
        )
        self.assertEqual(
            WorkflowExecution.all_objects.filter(status="accepted").count(), 3
        )

    def test_duplicate_sources_and_dispatch_are_idempotent(self):
        _case, _reminder, now = self._create_initial_three()
        enqueue_application_confirmation(self.application)
        case = VerificationCase.all_objects.get(application=self.application)
        queue_requirement_reminder(
            user=self.user,
            requirement=case.requirements.first(),
            now=now,
        )
        self.assertEqual(LifecycleEvent.all_objects.count(), 3)
        self.assertEqual(dispatch_due_events(), 3)
        self.assertEqual(dispatch_due_events(), 0)
        self.assertEqual(len(SyntheticActivepiecesTransport.deliveries), 3)
        self.assertEqual(DeliveryAttempt.all_objects.count(), 3)

    def test_source_event_and_outbox_registration_are_atomic(self):
        with patch(
            "tvg_automation.services.register_application_acknowledgment",
            side_effect=RuntimeError("Synthetic outbox failure."),
        ):
            with self.assertRaises(RuntimeError):
                enqueue_application_confirmation(self.application)

        self.assertFalse(
            ApplicationConfirmationEvent.all_objects.filter(
                application=self.application
            ).exists()
        )

    def test_transient_failure_retries_and_terminal_failure_can_be_replayed(self):
        enqueue_application_confirmation(self.application)
        event = LifecycleEvent.all_objects.get()
        SyntheticActivepiecesTransport.failures_remaining = 1
        self.assertEqual(dispatch_due_events(), 0)
        event.refresh_from_db()
        self.assertEqual(event.status, LifecycleEvent.Status.RETRY)
        self.assertEqual(event.attempt_count, 1)

        self.assertEqual(
            dispatch_due_events(now=timezone.now() + timedelta(minutes=2)), 1
        )
        event.refresh_from_db()
        self.assertEqual(event.status, LifecycleEvent.Status.DELIVERED)
        self.assertEqual(
            list(event.delivery_attempts.values_list("outcome", flat=True)),
            [DeliveryAttempt.Outcome.RETRY, DeliveryAttempt.Outcome.DELIVERED],
        )

        second = enqueue_lifecycle_event(
            source_model="synthetic.Source",
            source_id=uuid4(),
            company=self.company,
            event_type=APPLICATION_ACKNOWLEDGMENT,
            aggregate_type="application",
            aggregate_id=self.application.public_id,
            idempotency_key=f"synthetic-dead-letter:{uuid4()}",
            payload={},
            occurred_at=timezone.now(),
        )
        second.max_attempts = 1
        second.save(update_fields=("max_attempts",))
        SyntheticActivepiecesTransport.failures_remaining = 1
        dispatch_due_events()
        second.refresh_from_db()
        self.assertEqual(second.status, LifecycleEvent.Status.FAILED)
        replay_event(user=self.user, event=second, reason="Synthetic operator retry.")
        self.assertEqual(dispatch_due_events(), 1)
        second.refresh_from_db()
        self.assertEqual(second.status, LifecycleEvent.Status.DELIVERED)
        self.assertEqual(second.replays.count(), 1)

    def test_unauthorized_callbacks_and_state_transition_history(self):
        enqueue_application_confirmation(self.application)
        dispatch_due_events()
        event = LifecycleEvent.all_objects.get()
        client = Client()
        unauthorized = client.post(
            "/internal/automation/v1/executions/",
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(unauthorized.status_code, 404)
        arbitrary_update = self._signed_callback(
            event,
            status=WorkflowExecution.Status.RUNNING,
            key="callback-arbitrary-update-1",
            extra={"model": "employee.Employee", "field": "is_active"},
        )
        self.assertEqual(arbitrary_update.status_code, 400)
        invalid_event_id = self._signed_callback(
            event,
            status=WorkflowExecution.Status.RUNNING,
            key="callback-invalid-event-id-1",
            extra={"event_id": "not-a-uuid"},
        )
        self.assertEqual(invalid_event_id.status_code, 400)

        succeeded = self._signed_callback(
            event,
            status=WorkflowExecution.Status.SUCCEEDED,
            key="callback-success-1",
            message="Synthetic delivery completed.",
        )
        self.assertEqual(succeeded.status_code, 200)
        duplicate = self._signed_callback(
            event,
            status=WorkflowExecution.Status.SUCCEEDED,
            key="callback-success-1",
            message="Synthetic delivery completed.",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(WebhookReceipt.all_objects.count(), 1)

        out_of_order = self._signed_callback(
            event,
            status=WorkflowExecution.Status.RUNNING,
            key="callback-out-of-order-1",
        )
        self.assertEqual(out_of_order.status_code, 409)
        execution = event.workflow_executions.first()
        execution.refresh_from_db()
        self.assertEqual(execution.status, WorkflowExecution.Status.SUCCEEDED)

    def test_fast_authenticated_callback_is_recorded_during_delivery_lease(self):
        enqueue_application_confirmation(self.application)
        event = LifecycleEvent.all_objects.get()
        event.status = LifecycleEvent.Status.PROCESSING
        event.lease_expires_at = timezone.now() + timedelta(minutes=5)
        event.save(update_fields=("status", "lease_expires_at"))

        response = self._signed_callback(
            event,
            status=WorkflowExecution.Status.SUCCEEDED,
            key="callback-fast-success-1",
            execution_id="synthetic-fast-execution",
        )

        self.assertEqual(response.status_code, 200)
        execution = WorkflowExecution.all_objects.get(event=event)
        self.assertEqual(execution.status, WorkflowExecution.Status.SUCCEEDED)
        event.refresh_from_db()
        self.assertEqual(event.status, LifecycleEvent.Status.PROCESSING)

    def test_cancellation_and_cross_company_access_are_enforced(self):
        enqueue_application_confirmation(self.application)
        event = LifecycleEvent.all_objects.get()
        other_company = make_company("Synthetic Other Automation Employer")
        other_user = make_user("synthetic-other-automation-manager")
        make_employee(
            company=other_company,
            email="other.automation.manager@example.test",
            user=other_user,
        )
        other_user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="tvg_automation",
                codename__in=("cancel_lifecycle_event", "replay_lifecycle_event"),
            )
        )
        other_user = type(other_user).objects.get(pk=other_user.pk)
        with self.assertRaises(PermissionDenied):
            cancel_event(user=other_user, event=event, reason="Cross-company attempt.")

        event.status = LifecycleEvent.Status.PROCESSING
        event.lease_expires_at = timezone.now() + timedelta(minutes=5)
        event.save(update_fields=("status", "lease_expires_at"))
        with self.assertRaises(ValidationError):
            cancel_event(user=self.user, event=event, reason="Unsafe in-flight cancel.")
        event.status = LifecycleEvent.Status.PENDING
        event.lease_expires_at = None
        event.save(update_fields=("status", "lease_expires_at"))

        cancel_event(
            user=self.user, event=event, reason="Synthetic application withdrawn."
        )
        self.assertEqual(dispatch_due_events(), 0)
        event.refresh_from_db()
        self.assertEqual(event.status, LifecycleEvent.Status.CANCELLED)
        self.assertEqual(event.delivery_attempts.count(), 0)
        with self.assertRaises(ValidationError):
            replay_event(user=self.user, event=event, reason="Unsafe replay attempt.")

    @override_settings(
        TVG_AUTOMATION_ENVIRONMENT="staging",
        TVG_ACTIVEPIECES_WEBHOOK_URL="https://activepieces.example.invalid/hook",
        TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET=(
            "change-me-independent-automation-secret"
        ),
        TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET=(
            "change-me-independent-automation-secret"
        ),
        TVG_AUTOMATION_TRANSPORT_BACKEND=(
            "tvg_automation.transport.DisabledActivepiecesTransport"
        ),
    )
    def test_staging_configuration_rejects_placeholders_and_disabled_transport(self):
        finding_ids = {finding.id for finding in automation_configuration_check(None)}
        self.assertTrue(
            {
                "tvg_automation.E002",
                "tvg_automation.E003",
                "tvg_automation.E007",
                "tvg_automation.E008",
                "tvg_automation.E009",
            }.issubset(finding_ids)
        )

    def test_human_approval_and_template_versions_fail_closed(self):
        pending = enqueue_lifecycle_event(
            source_model="synthetic.UnapprovedVerification",
            source_id=uuid4(),
            company=self.company,
            event_type=VERIFICATION_REQUEST,
            aggregate_type="verification_case",
            aggregate_id=uuid4(),
            idempotency_key=f"synthetic-unapproved:{uuid4()}",
            payload={},
            occurred_at=timezone.now(),
        )
        self.assertEqual(pending.status, LifecycleEvent.Status.AWAITING_APPROVAL)
        self.assertEqual(dispatch_due_events(), 0)

        approved = AutomationMessageTemplate.all_objects.get(
            workflow_key="application_acknowledgment"
        )
        approved.subject_template = "Edited after approval"
        with self.assertRaises(ValidationError):
            approved.save()
        version_two = AutomationMessageTemplate.all_objects.create(
            company=self.company,
            workflow_key="application_acknowledgment",
            version=2,
            subject_template="Application received: {job_title}",
            body_template="Hello {candidate_name}. Application {application_id} was received.",
        )
        version_two = approve_template(user=self.user, template=version_two)
        approved.refresh_from_db()
        self.assertFalse(approved.is_active)
        self.assertTrue(version_two.is_active)
