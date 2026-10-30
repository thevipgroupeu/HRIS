from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .events import (
    APPLICATION_ACKNOWLEDGMENT,
    DOCUMENT_REMINDER,
    VERIFICATION_REQUEST,
    definition_for,
)
from .models import (
    AutomationMessageTemplate,
    DeliveryAttempt,
    EventReplay,
    LifecycleEvent,
    WorkflowExecution,
)
from .rendering import render_message
from .signing import body_signature, signature
from .transport import TransportError

SOURCE_APPLICATION_CONFIRMATION = "tvg_careers.ApplicationConfirmationEvent"
SOURCE_VERIFICATION_NOTIFICATION = "tvg_erasmus.VerificationNotification"


def _max_attempts():
    return max(1, min(int(getattr(settings, "TVG_AUTOMATION_MAX_ATTEMPTS", 5)), 20))


def _employee_for_user(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    try:
        return user.employee_get
    except Exception as exc:
        raise PermissionDenied("An employee identity is required.") from exc


def _same_company(employee, company):
    employee_company = employee.get_company()
    return bool(employee_company and employee_company.pk == company.pk)


@transaction.atomic
def approve_template(*, user, template):
    if not user.has_perm("tvg_automation.approve_message_template"):
        raise PermissionDenied
    employee = _employee_for_user(user)
    template = AutomationMessageTemplate.all_objects.select_for_update(of=("self",)).get(
        pk=template.pk
    )
    if not _same_company(employee, template.company):
        raise PermissionDenied
    if template.is_approved:
        return template
    template.approved_by = employee
    template.approved_at = timezone.now()
    template.is_active = True
    template.save()
    AutomationMessageTemplate.all_objects.filter(
        company=template.company,
        workflow_key=template.workflow_key,
        is_active=True,
    ).exclude(pk=template.pk).update(is_active=False)
    return template


def enqueue_lifecycle_event(
    *,
    source_model,
    source_id,
    company,
    event_type,
    aggregate_type,
    aggregate_id,
    idempotency_key,
    payload,
    occurred_at,
    approved_by=None,
    approved_at=None,
):
    definition = definition_for(event_type)
    if approved_by and not _same_company(approved_by, company):
        raise ValidationError("The event approver must belong to this company.")
    if definition.human_approval_required and not (approved_by and approved_at):
        status = LifecycleEvent.Status.AWAITING_APPROVAL
    else:
        status = LifecycleEvent.Status.PENDING
    event, created = LifecycleEvent.all_objects.get_or_create(
        source_model=source_model,
        source_id=source_id,
        defaults={
            "company": company,
            "event_type": event_type,
            "workflow_key": definition.workflow_key,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "occurred_at": occurred_at,
            "human_approval_required": definition.human_approval_required,
            "approved_by": approved_by,
            "approved_at": approved_at,
            "status": status,
            "max_attempts": _max_attempts(),
            "next_attempt_at": timezone.now(),
        },
    )
    if not created:
        expected = {
            "company_id": company.pk,
            "event_type": event_type,
            "workflow_key": definition.workflow_key,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "occurred_at": occurred_at,
            "human_approval_required": definition.human_approval_required,
        }
        if any(getattr(event, field) != value for field, value in expected.items()):
            raise ValidationError(
                "This source was already registered with different event facts."
            )
    return event


def register_application_acknowledgment(source):
    application = source.application
    return enqueue_lifecycle_event(
        source_model=SOURCE_APPLICATION_CONFIRMATION,
        source_id=source.event_id,
        company=application.opening.venture.legal_employer,
        event_type=APPLICATION_ACKNOWLEDGMENT,
        aggregate_type="application",
        aggregate_id=application.public_id,
        idempotency_key=f"application-acknowledgment:{application.public_id}:v1",
        payload=dict(source.payload),
        occurred_at=source.occurred_at,
    )


def register_verification_notification(source):
    case = source.case
    if source.event_type == "verification.requested":
        event_type = VERIFICATION_REQUEST
        approved_by = case.requested_by
        approved_at = case.requested_at
    elif source.event_type == "verification.reminder":
        event_type = DOCUMENT_REMINDER
        approved_by = None
        approved_at = None
    else:
        return None
    payload = {
        "notification_id": str(source.event_id),
        "case_id": str(case.public_id),
        "requirement_id": (
            str(source.requirement.public_id) if source.requirement_id else None
        ),
        "candidate_message": source.candidate_message,
        "next_action": source.next_action,
    }
    return enqueue_lifecycle_event(
        source_model=SOURCE_VERIFICATION_NOTIFICATION,
        source_id=source.event_id,
        company=case.application.opening.venture.legal_employer,
        event_type=event_type,
        aggregate_type="verification_case",
        aggregate_id=case.public_id,
        idempotency_key=source.idempotency_key,
        payload=payload,
        occurred_at=source.occurred_at,
        approved_by=approved_by,
        approved_at=approved_at,
    )


def materialize_existing_initial_events():
    """Backfill only missing outbox wrappers around pre-Phase-15 source events."""

    from tvg_careers.models import ApplicationConfirmationEvent
    from tvg_erasmus.models import VerificationNotification

    known_application_ids = LifecycleEvent.all_objects.filter(
        source_model=SOURCE_APPLICATION_CONFIRMATION
    ).values_list("source_id", flat=True)
    for source in (
        ApplicationConfirmationEvent.all_objects.exclude(
            event_id__in=known_application_ids
        )
        .select_related("application__opening__venture")
        .order_by("pk")[:100]
    ):
        register_application_acknowledgment(source)

    known_notification_ids = LifecycleEvent.all_objects.filter(
        source_model=SOURCE_VERIFICATION_NOTIFICATION
    ).values_list("source_id", flat=True)
    for source in (
        VerificationNotification.all_objects.exclude(
            event_id__in=known_notification_ids
        )
        .filter(event_type__in=("verification.requested", "verification.reminder"))
        .select_related(
            "case__requested_by",
            "case__application__opening__venture",
            "requirement",
        )
        .order_by("pk")[:100]
    ):
        register_verification_notification(source)


def _transport():
    backend = getattr(
        settings,
        "TVG_AUTOMATION_TRANSPORT_BACKEND",
        "tvg_automation.transport.DisabledActivepiecesTransport",
    )
    return import_string(backend)()


def _claim_due_event(now):
    lease_until = now + timedelta(minutes=5)
    with transaction.atomic():
        event = (
            LifecycleEvent.all_objects.select_for_update(of=("self",))
            .filter(
                status__in=(
                    LifecycleEvent.Status.PENDING,
                    LifecycleEvent.Status.RETRY,
                    LifecycleEvent.Status.PROCESSING,
                ),
                next_attempt_at__lte=now,
            )
            .filter(
                models.Q(
                    status__in=(
                        LifecycleEvent.Status.PENDING,
                        LifecycleEvent.Status.RETRY,
                    )
                )
                | models.Q(lease_expires_at__lte=now)
            )
            .order_by("next_attempt_at", "pk")
            .first()
        )
        if not event:
            return None
        event.status = LifecycleEvent.Status.PROCESSING
        event.lease_expires_at = lease_until
        event.save(update_fields=("status", "lease_expires_at"))
        return event.pk


def _envelope(event, rendered):
    environment = getattr(settings, "TVG_AUTOMATION_ENVIRONMENT", "development")
    return {
        "schema": "eu.thevipgroup.lifecycle-event",
        "schema_version": event.schema_version,
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "environment": environment,
        "idempotency_key": event.idempotency_key,
        "company_id": str(event.company_id),
        "aggregate": {"type": event.aggregate_type, "id": str(event.aggregate_id)},
        "workflow": {
            "key": event.workflow_key,
            "human_approval_required": event.human_approval_required,
            "approved": bool(event.approved_by_id and event.approved_at),
            "approved_at": event.approved_at.isoformat() if event.approved_at else None,
        },
        "message": {
            "channel": "email",
            "to": {
                "email": rendered.recipient_email,
                "name": rendered.recipient_name,
            },
            "from": rendered.from_email,
            "reply_to": rendered.reply_to,
            "subject": rendered.subject,
            "text": rendered.body,
            "template": {
                "id": rendered.template_id,
                "version": rendered.template_version,
            },
        },
        "payload": event.payload,
    }


def _record_failure(event, started_at, exc, now):
    attempt_number = event.attempt_count + 1
    attempt_sequence = (
        DeliveryAttempt.all_objects.filter(event=event).aggregate(
            max_sequence=models.Max("sequence")
        )["max_sequence"]
        or 0
    ) + 1
    retryable = isinstance(exc, TransportError) and exc.retryable
    status_code = exc.status if isinstance(exc, TransportError) else None
    error_code = (
        exc.code
        if isinstance(exc, TransportError)
        else "configuration_or_rendering_error"
    )
    error_message = str(exc)[:500]
    exhausted = attempt_number >= event.max_attempts
    outcome = (
        DeliveryAttempt.Outcome.RETRY
        if retryable and not exhausted
        else DeliveryAttempt.Outcome.FAILED
    )
    DeliveryAttempt.all_objects.create(
        event=event,
        sequence=attempt_sequence,
        environment=getattr(settings, "TVG_AUTOMATION_ENVIRONMENT", "development"),
        outcome=outcome,
        started_at=started_at,
        completed_at=now,
        response_status=status_code,
        error_code=error_code,
        error_message=error_message,
    )
    if outcome == DeliveryAttempt.Outcome.RETRY:
        delay = min(2 ** max(0, attempt_number - 1), 60)
        event.status = LifecycleEvent.Status.RETRY
        event.next_attempt_at = now + timedelta(minutes=delay)
    else:
        event.status = LifecycleEvent.Status.FAILED
    event.attempt_count = attempt_number
    event.lease_expires_at = None
    event.last_error_code = error_code
    event.last_error = error_message
    event.save(
        update_fields=(
            "status",
            "attempt_count",
            "lease_expires_at",
            "next_attempt_at",
            "last_error_code",
            "last_error",
        )
    )
    _mark_source_failed(event, error_message)


def _mark_source_failed(event, error_message):
    values = {
        "delivery_attempts": models.F("delivery_attempts") + 1,
        "last_error": error_message[:255],
    }
    if event.source_model == SOURCE_APPLICATION_CONFIRMATION:
        from tvg_careers.models import ApplicationConfirmationEvent

        ApplicationConfirmationEvent.all_objects.filter(
            event_id=event.source_id
        ).update(**values)
    elif event.source_model == SOURCE_VERIFICATION_NOTIFICATION:
        from tvg_erasmus.models import VerificationNotification

        VerificationNotification.all_objects.filter(event_id=event.source_id).update(
            **values
        )


def _mark_source_published(event, now):
    if event.source_model == SOURCE_APPLICATION_CONFIRMATION:
        from tvg_careers.models import ApplicationConfirmationEvent

        ApplicationConfirmationEvent.all_objects.filter(
            event_id=event.source_id
        ).update(
            published_at=now,
            delivery_attempts=models.F("delivery_attempts") + 1,
            last_error="",
        )
    elif event.source_model == SOURCE_VERIFICATION_NOTIFICATION:
        from tvg_erasmus.models import VerificationNotification

        VerificationNotification.all_objects.filter(event_id=event.source_id).update(
            published_at=now,
            delivery_attempts=models.F("delivery_attempts") + 1,
            last_error="",
        )


def _dispatch_claimed(event_id):
    started_at = timezone.now()
    event = LifecycleEvent.all_objects.select_related("company").get(pk=event_id)
    if event.status != LifecycleEvent.Status.PROCESSING:
        return False
    try:
        if event.human_approval_required and not (
            event.approved_by_id and event.approved_at
        ):
            raise ValidationError("Required human approval is missing.")
        rendered = render_message(event)
        envelope = _envelope(event, rendered)
        body = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        timestamp = str(int(started_at.timestamp()))
        secret = getattr(settings, "TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET", "")
        if not secret:
            raise TransportError(
                "Outbound webhook signing secret is not configured.",
                code="configuration_missing",
                retryable=False,
            )
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": event.idempotency_key,
            "X-TVG-Event-ID": str(event.event_id),
            "X-TVG-Event-Type": event.event_type,
            "X-TVG-Timestamp": timestamp,
            "X-TVG-Signature": signature(secret, timestamp, body),
            # Activepieces' current native HMAC webhook trigger signs the raw
            # body only. Keep the stronger timestamp-bound signature above for
            # custom verification and expose this compatibility signature so
            # the standard trigger can reject unauthenticated traffic before a
            # flow starts. The flow must still enforce timestamp freshness.
            "X-TVG-Body-Signature": body_signature(secret, body),
        }
        response = _transport().send(body, headers)
    except Exception as caught:
        if isinstance(caught, (TransportError, ValidationError)):
            exc = caught
        else:
            exc = TransportError(
                "Automation dispatch failed before delivery.",
                code="unexpected_dispatch_error",
                retryable=False,
            )
        with transaction.atomic():
            locked = LifecycleEvent.all_objects.select_for_update(of=("self",)).get(pk=event_id)
            if locked.status == LifecycleEvent.Status.PROCESSING:
                _record_failure(locked, started_at, exc, timezone.now())
        return False

    now = timezone.now()
    with transaction.atomic():
        event = LifecycleEvent.all_objects.select_for_update(of=("self",)).get(pk=event_id)
        if event.status != LifecycleEvent.Status.PROCESSING:
            return False
        attempt_number = event.attempt_count + 1
        attempt_sequence = (
            DeliveryAttempt.all_objects.filter(event=event).aggregate(
                max_sequence=models.Max("sequence")
            )["max_sequence"]
            or 0
        ) + 1
        external_id = response.external_execution_id
        DeliveryAttempt.all_objects.create(
            event=event,
            sequence=attempt_sequence,
            environment=getattr(settings, "TVG_AUTOMATION_ENVIRONMENT", "development"),
            outcome=DeliveryAttempt.Outcome.DELIVERED,
            started_at=started_at,
            completed_at=now,
            response_status=response.status,
            external_execution_id=external_id,
        )
        if external_id:
            WorkflowExecution.all_objects.get_or_create(
                event=event,
                external_execution_id=external_id,
                defaults={
                    "workflow_key": event.workflow_key,
                    "status": WorkflowExecution.Status.ACCEPTED,
                    "started_at": now,
                },
            )
        event.status = LifecycleEvent.Status.DELIVERED
        event.attempt_count = attempt_number
        event.delivered_at = now
        event.lease_expires_at = None
        event.last_error_code = ""
        event.last_error = ""
        event.save(
            update_fields=(
                "status",
                "attempt_count",
                "delivered_at",
                "lease_expires_at",
                "last_error_code",
                "last_error",
            )
        )
        _mark_source_published(event, now)
    return True


def dispatch_due_events(*, limit=100, now=None):
    if not getattr(settings, "TVG_AUTOMATION_ENABLED", False):
        return 0
    now = now or timezone.now()
    delivered = 0
    for _index in range(max(0, min(int(limit), 500))):
        event_id = _claim_due_event(now)
        if not event_id:
            break
        delivered += int(_dispatch_claimed(event_id))
    return delivered


@transaction.atomic
def authorize_event(*, user, event):
    if not user.has_perm("tvg_automation.authorize_lifecycle_event"):
        raise PermissionDenied
    employee = _employee_for_user(user)
    event = LifecycleEvent.all_objects.select_for_update(of=("self",)).get(pk=event.pk)
    if not _same_company(employee, event.company):
        raise PermissionDenied
    if event.status != LifecycleEvent.Status.AWAITING_APPROVAL:
        raise ValidationError("Only events awaiting approval can be authorized.")
    event.approved_by = employee
    event.approved_at = timezone.now()
    event.status = LifecycleEvent.Status.PENDING
    event.next_attempt_at = timezone.now()
    event.save()
    return event


@transaction.atomic
def cancel_event(*, user, event, reason):
    if not user.has_perm("tvg_automation.cancel_lifecycle_event"):
        raise PermissionDenied
    employee = _employee_for_user(user)
    event = LifecycleEvent.all_objects.select_for_update(of=("self",)).get(pk=event.pk)
    if not _same_company(employee, event.company):
        raise PermissionDenied
    if event.status in {
        LifecycleEvent.Status.DELIVERED,
        LifecycleEvent.Status.CANCELLED,
    }:
        raise ValidationError("This event can no longer be cancelled.")
    if event.status == LifecycleEvent.Status.PROCESSING:
        raise ValidationError("An event currently being delivered cannot be cancelled.")
    if not reason.strip():
        raise ValidationError("A cancellation reason is required.")
    event.status = LifecycleEvent.Status.CANCELLED
    event.cancelled_by = employee
    event.cancelled_at = timezone.now()
    event.cancellation_reason = reason.strip()[:500]
    event.lease_expires_at = None
    event.save()
    return event


@transaction.atomic
def replay_event(*, user, event, reason):
    if not user.has_perm("tvg_automation.replay_lifecycle_event"):
        raise PermissionDenied
    employee = _employee_for_user(user)
    event = LifecycleEvent.all_objects.select_for_update(of=("self",)).get(pk=event.pk)
    if not _same_company(employee, event.company):
        raise PermissionDenied
    if event.status != LifecycleEvent.Status.FAILED:
        raise ValidationError("Only failed events can be replayed.")
    if event.replay_count >= 5:
        raise ValidationError("The replay safety limit has been reached.")
    if not reason.strip():
        raise ValidationError("A replay reason is required.")
    EventReplay.all_objects.create(
        event=event,
        requested_by=employee,
        reason=reason.strip()[:500],
        previous_error_code=event.last_error_code,
        previous_error=event.last_error,
    )
    event.status = LifecycleEvent.Status.RETRY
    event.attempt_count = 0
    event.next_attempt_at = timezone.now()
    event.lease_expires_at = None
    event.last_error_code = ""
    event.last_error = ""
    event.replay_count += 1
    event.save()
    return event
