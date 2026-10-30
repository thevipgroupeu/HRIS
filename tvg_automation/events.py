"""Versioned lifecycle event catalogue and explicit approval boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventDefinition:
    event_type: str
    workflow_key: str
    human_approval_required: bool = False
    implemented: bool = False


APPLICATION_ACKNOWLEDGMENT = "career.application_submitted.v1"
VERIFICATION_REQUEST = "erasmus.verification_requested.v1"
DOCUMENT_REMINDER = "erasmus.document_reminder_requested.v1"


EVENT_DEFINITIONS = {
    item.event_type: item
    for item in (
        EventDefinition(
            APPLICATION_ACKNOWLEDGMENT,
            "application_acknowledgment",
            implemented=True,
        ),
        EventDefinition(
            VERIFICATION_REQUEST,
            "shortlisted_verification_request",
            human_approval_required=True,
            implemented=True,
        ),
        EventDefinition(
            DOCUMENT_REMINDER,
            "document_reminder",
            implemented=True,
        ),
        EventDefinition(
            "interview.invitation_requested.v1",
            "verified_candidate_interview_invitation",
            human_approval_required=True,
        ),
        EventDefinition("interview.reminder_due.v1", "interview_reminder"),
        EventDefinition(
            "recruitment.decision_message_authorized.v1",
            "decision_email",
            human_approval_required=True,
        ),
        EventDefinition("erasmus.date_confirmation_requested.v1", "date_confirmation"),
        EventDefinition(
            "erasmus.acceptance_notification_authorized.v1",
            "acceptance_letter_notification",
            human_approval_required=True,
        ),
        EventDefinition(
            "erasmus.learning_agreement_follow_up_due.v1",
            "learning_agreement_follow_up",
        ),
        EventDefinition(
            "erasmus.legal_document_reminder_due.v1", "legal_document_reminder"
        ),
        EventDefinition(
            "erasmus.accommodation_follow_up_requested.v1",
            "accommodation_support_request",
        ),
        EventDefinition("onboarding.pre_arrival_welcome_due.v1", "pre_arrival_welcome"),
        EventDefinition("reporting.reminder_due.v1", "reporting_reminder"),
        EventDefinition(
            "offboarding.completion_reminder_due.v1", "internship_completion_reminder"
        ),
        EventDefinition(
            "offboarding.asset_return_task_requested.v1",
            "asset_return_task",
            human_approval_required=True,
        ),
        EventDefinition(
            "offboarding.access_deprovisioning_requested.v1",
            "account_deprovisioning_task",
            human_approval_required=True,
        ),
    )
}


def definition_for(event_type: str) -> EventDefinition:
    try:
        return EVENT_DEFINITIONS[event_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported lifecycle event type: {event_type}") from exc
