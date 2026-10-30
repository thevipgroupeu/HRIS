"""Resolve approved message content without storing recipient PII in the outbox."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .events import APPLICATION_ACKNOWLEDGMENT, DOCUMENT_REMINDER, VERIFICATION_REQUEST
from .models import AutomationMessageTemplate, LifecycleEvent

PLACEHOLDERS = {
    "application_acknowledgment": {
        "candidate_name",
        "job_title",
        "submitted_at",
        "application_id",
    },
    "shortlisted_verification_request": {
        "candidate_name",
        "job_title",
        "due_date",
        "candidate_message",
        "next_action",
        "application_id",
    },
    "document_reminder": {
        "candidate_name",
        "job_title",
        "due_date",
        "document_title",
        "candidate_message",
        "next_action",
        "application_id",
    },
}


def allowed_placeholders(workflow_key):
    return PLACEHOLDERS.get(workflow_key, set())


class StrictValues(dict):
    def __missing__(self, key):
        raise KeyError(key)


@dataclass(frozen=True)
class RenderedMessage:
    recipient_email: str
    recipient_name: str
    subject: str
    body: str
    from_email: str
    reply_to: str
    template_id: str
    template_version: int


def _application_context(application):
    candidate_name = (
        application.candidate.name or application.person.preferred_name
    ).strip()
    return {
        "candidate_name": candidate_name,
        "job_title": application.opening.title,
        "submitted_at": application.submitted_at.date().isoformat(),
        "application_id": str(application.public_id),
    }


def _resolve_target(event):
    from tvg_careers.models import Application
    from tvg_erasmus.models import VerificationCase, VerificationRequirement

    if event.event_type == APPLICATION_ACKNOWLEDGMENT:
        application = (
            Application.all_objects.select_related("person", "candidate", "opening")
            .filter(
                public_id=event.aggregate_id,
                opening__venture__legal_employer=event.company,
            )
            .first()
        )
        if not application:
            raise ValidationError("The application event target no longer exists.")
        return application, _application_context(application)

    if event.event_type in {VERIFICATION_REQUEST, DOCUMENT_REMINDER}:
        case = (
            VerificationCase.all_objects.select_related(
                "application__person",
                "application__candidate",
                "application__opening",
            )
            .filter(
                public_id=event.aggregate_id,
                application__opening__venture__legal_employer=event.company,
            )
            .first()
        )
        if not case:
            raise ValidationError("The verification event target no longer exists.")
        application = case.application
        context = _application_context(application)
        context.update(
            {
                "due_date": case.due_at.date().isoformat() if case.due_at else "",
                "candidate_message": str(event.payload.get("candidate_message", "")),
                "next_action": str(event.payload.get("next_action", "")),
            }
        )
        if event.event_type == DOCUMENT_REMINDER:
            requirement_id = event.payload.get("requirement_id")
            requirement = VerificationRequirement.all_objects.filter(
                public_id=requirement_id,
                case=case,
            ).first()
            if not requirement:
                raise ValidationError("The document reminder target no longer exists.")
            context["document_title"] = requirement.title
        return application, context
    raise ValidationError("This lifecycle event has no approved message renderer.")


def render_message(event: LifecycleEvent) -> RenderedMessage:
    application, context = _resolve_target(event)
    template = (
        AutomationMessageTemplate.all_objects.filter(
            company=event.company,
            workflow_key=event.workflow_key,
            is_active=True,
            approved_at__isnull=False,
            approved_by__isnull=False,
        )
        .order_by("-version")
        .first()
    )
    if not template:
        raise ValidationError(
            f"No active approved template exists for {event.workflow_key}."
        )
    values = StrictValues(context)
    try:
        subject = template.subject_template.format_map(values)
        body = template.body_template.format_map(values)
    except (KeyError, ValueError) as exc:
        raise ValidationError("The approved template could not be rendered.") from exc
    if not subject.strip() or not body.strip():
        raise ValidationError("The approved template rendered empty content.")
    return RenderedMessage(
        recipient_email=application.person.primary_email,
        recipient_name=context["candidate_name"],
        subject=subject,
        body=body,
        from_email=template.from_email,
        reply_to=template.reply_to,
        template_id=str(template.public_id),
        template_version=template.version,
    )
