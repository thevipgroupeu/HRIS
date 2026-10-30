from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from base.auth_backends import get_allowed_company_ids
from employee.models import Employee
from recruitment.models import CandidateDocument, CandidateDocumentRequest
from tvg_careers.models import Application
from tvg_careers.verification_document_security import inspect_verification_document
from tvg_erasmus.models import (
    AcceptanceLetter,
    AcceptanceLetterTemplate,
    AcceptanceNotification,
    DurationException,
    ErasmusArrangementPolicy,
    ErasmusCase,
    ErasmusCaseEvent,
    ScreeningDecision,
    ScreeningEvent,
    VerificationAuditEvent,
    VerificationCase,
    VerificationChecklist,
    VerificationEvidence,
    VerificationNotification,
    VerificationRequirement,
    VerificationRequirementTemplate,
)
from tvg_erasmus.scanning import scan_verification_upload

REQUIRED_CATEGORIES = {
    VerificationRequirementTemplate.Category.IDENTITY,
    VerificationRequirementTemplate.Category.STUDENT_STATUS,
    VerificationRequirementTemplate.Category.UNIVERSITY,
}


@dataclass(frozen=True)
class InterviewEligibility:
    eligible: bool
    reason: str


def _employee_for_user(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    if not employee:
        raise PermissionDenied
    return employee


def require_application_access(user, application, permission):
    employee = _employee_for_user(user)
    company_id = application.opening.venture.legal_employer_id
    if not user.is_superuser:
        if company_id not in get_allowed_company_ids(user):
            raise PermissionDenied
        if not user.has_perm(permission):
            raise PermissionDenied
        related = application.opening.recruitment.recruitment_managers.filter(
            pk=employee.pk
        ).exists()
        if application.candidate.stage_id_id:
            related = (
                related
                or application.candidate.stage_id.stage_managers.filter(
                    pk=employee.pk
                ).exists()
            )
        if not related:
            raise PermissionDenied
    return employee


def visible_applications(user):
    if not getattr(user, "is_authenticated", False):
        return Application.all_objects.none()
    queryset = Application.all_objects.select_related(
        "person",
        "candidate__stage_id",
        "opening__recruitment",
        "opening__venture",
    )
    if user.is_superuser:
        return queryset
    company_ids = get_allowed_company_ids(user)
    if not company_ids or not user.has_perm("tvg_erasmus.view_screeningdecision"):
        return queryset.none()
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    if not employee:
        return queryset.none()
    return (
        queryset.filter(opening__venture__legal_employer_id__in=company_ids)
        .filter(
            Q(opening__recruitment__recruitment_managers=employee)
            | Q(candidate__stage_id__stage_managers=employee)
        )
        .distinct()
    )


@transaction.atomic
def _notification(
    *,
    case,
    event_type,
    idempotency_key,
    candidate_message,
    next_action="",
    requirement=None,
):
    notification, _created = VerificationNotification.all_objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "case": case,
            "requirement": requirement,
            "event_type": event_type,
            "candidate_message": candidate_message,
            "next_action": next_action,
        },
    )
    # Register the versioned Phase 15 event in this transaction. Network
    # delivery remains scheduler-owned and cannot roll back an HR action.
    from tvg_automation.services import register_verification_notification

    register_verification_notification(notification)
    return notification


def _audit(
    *,
    case,
    event_type,
    actor_type,
    actor_reference=None,
    requirement=None,
    from_status="",
    to_status="",
    candidate_message="",
    internal_note="",
):
    return VerificationAuditEvent.all_objects.create(
        case=case,
        requirement=requirement,
        event_type=event_type,
        actor_type=actor_type,
        actor_reference=actor_reference,
        from_status=from_status,
        to_status=to_status,
        candidate_message=candidate_message,
        internal_note=internal_note,
    )


def _validate_checklist(checklist):
    templates = list(
        checklist.requirement_templates.filter(is_active=True).order_by(
            "sequence", "pk"
        )
    )
    required_categories = {item.category for item in templates if item.required}
    missing = REQUIRED_CATEGORIES - required_categories
    if missing:
        raise ValidationError(
            "The verification checklist must require identity, student-status and university confirmation."
        )
    return templates


def _create_native_requirement(candidate, template):
    formats = list(template.allowed_formats)
    request = CandidateDocumentRequest._base_manager.create(
        title=template.title,
        format=formats[0] if len(formats) == 1 else "any",
        max_size=template.max_size_mb,
        description=template.instructions,
    )
    request.candidate_id.add(candidate)
    document = CandidateDocument._base_manager.get(
        candidate_id=candidate,
        document_request_id=request,
    )
    return request, document


def _request_verification_locked(application, employee):
    existing = VerificationCase.all_objects.filter(application=application).first()
    if existing:
        return existing
    checklist = VerificationChecklist.all_objects.filter(
        recruitment=application.opening.recruitment,
        is_active=True,
    ).first()
    if not checklist:
        if application.opening.erasmus_plus_applicable:
            raise ValidationError(
                "An active verification checklist is required before shortlisting this Erasmus+ application."
            )
        return VerificationCase.all_objects.create(
            application=application,
            checklist=None,
            status=VerificationCase.Status.NOT_REQUIRED,
            requested_by=employee,
            requested_at=timezone.now(),
            completed_at=timezone.now(),
        )

    templates = _validate_checklist(checklist)
    now = timezone.now()
    case = VerificationCase.all_objects.create(
        application=application,
        checklist=checklist,
        status=VerificationCase.Status.REQUESTED,
        requested_at=now,
        requested_by=employee,
        due_at=now + timedelta(days=checklist.due_days),
    )
    for template in templates:
        native_request, native_document = _create_native_requirement(
            application.candidate, template
        )
        VerificationRequirement.all_objects.create(
            case=case,
            template=template,
            native_document_request=native_request,
            native_document=native_document,
            code=template.code,
            category=template.category,
            title=template.title,
            instructions=template.instructions,
            required=template.required,
            allowed_formats=list(template.allowed_formats),
            max_size_mb=template.max_size_mb,
            status=(
                VerificationCase.Status.REQUESTED
                if template.required
                else VerificationCase.Status.NOT_REQUIRED
            ),
        )
    _audit(
        case=case,
        event_type="verification.requested",
        actor_type=VerificationAuditEvent.ActorType.STAFF,
        actor_reference=None,
        to_status=case.status,
        candidate_message="Verification documents are required before an interview can be arranged.",
    )
    _notification(
        case=case,
        event_type="verification.requested",
        idempotency_key=f"verification-requested:{case.public_id}",
        candidate_message="Please submit the requested verification documents.",
        next_action="Upload each required document through your secure application dashboard.",
    )
    return case


@transaction.atomic
def screen_application(
    *, user, application, action, applicant_message="", internal_note=""
):
    application = (
        Application.all_objects.select_for_update(of=("self",))
        .select_related(
            "candidate__stage_id",
            "opening__recruitment",
            "opening__venture",
            "person",
        )
        .get(pk=application.pk)
    )
    employee = require_application_access(
        user, application, "tvg_erasmus.screen_application"
    )
    if application.withdrawn_at or application.candidate.canceled:
        raise ValidationError("An inactive application cannot be screened.")
    if action not in ScreeningDecision.Status.values:
        raise ValidationError("Unsupported screening action.")
    if action == ScreeningDecision.Status.INITIAL_SCREENING:
        raise ValidationError("Initial screening is not a decision action.")
    if (
        action == ScreeningDecision.Status.INFORMATION_REQUESTED
        and not applicant_message.strip()
    ):
        raise ValidationError("A candidate-facing information request is required.")

    (
        screening,
        _created,
    ) = ScreeningDecision.all_objects.select_for_update(of=("self",)).get_or_create(
        application=application
    )
    if screening.status == ScreeningDecision.Status.REJECTED:
        raise ValidationError("A rejected screening decision is final.")
    previous = screening.status
    screening.status = action
    screening.applicant_message = applicant_message.strip()[:500]
    screening.internal_note = internal_note.strip()
    screening.decided_by = employee
    screening.decided_at = timezone.now()
    screening.save()
    ScreeningEvent.all_objects.create(
        screening=screening,
        from_status=previous,
        to_status=action,
        actor=employee,
        applicant_message=screening.applicant_message,
        internal_note=screening.internal_note,
    )

    if action == ScreeningDecision.Status.SHORTLISTED:
        _request_verification_locked(application, employee)
    elif action == ScreeningDecision.Status.REJECTED:
        application.candidate.canceled = True
        application.candidate.save()
        close_verification_for_inactive_application(
            application,
            reason="screening_rejected",
        )
    return screening


@transaction.atomic
def request_verification(*, user, application):
    application = (
        Application.all_objects.select_for_update(of=("self",))
        .select_related(
            "candidate__stage_id", "opening__recruitment", "opening__venture"
        )
        .get(pk=application.pk)
    )
    employee = require_application_access(
        user, application, "tvg_erasmus.manage_verification"
    )
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    if (
        application.withdrawn_at
        or application.candidate.canceled
        or not screening
        or screening.status != ScreeningDecision.Status.SHORTLISTED
    ):
        raise ValidationError("Verification can be requested only after shortlisting.")
    return _request_verification_locked(application, employee)


def _recalculate_case(case):
    requirements = list(
        VerificationRequirement.all_objects.filter(case=case, required=True)
    )
    statuses = {item.status for item in requirements}
    previous = case.status
    if requirements and statuses == {VerificationCase.Status.VERIFIED}:
        case.status = VerificationCase.Status.VERIFIED
        case.completed_at = case.completed_at or timezone.now()
    elif VerificationCase.Status.REJECTED in statuses:
        case.status = VerificationCase.Status.REJECTED
        case.completed_at = case.completed_at or timezone.now()
    elif VerificationCase.Status.REVISION_NEEDED in statuses:
        case.status = VerificationCase.Status.REVISION_NEEDED
        case.completed_at = None
    elif VerificationCase.Status.UNDER_REVIEW in statuses:
        case.status = VerificationCase.Status.UNDER_REVIEW
        case.completed_at = None
    elif VerificationCase.Status.SUBMITTED in statuses:
        case.status = VerificationCase.Status.SUBMITTED
        case.submitted_at = case.submitted_at or timezone.now()
        case.completed_at = None
    else:
        case.status = VerificationCase.Status.REQUESTED
        case.completed_at = None
    case.save()
    if case.status in {
        VerificationCase.Status.VERIFIED,
        VerificationCase.Status.REJECTED,
    }:
        _schedule_evidence_deletion(case)
    return previous, case.status


def _schedule_evidence_deletion(case, *, now=None):
    if not case.checklist_id:
        return
    now = now or timezone.now()
    delete_after = now + timedelta(days=case.checklist.retention_days)
    VerificationEvidence.all_objects.filter(
        requirement__case=case,
        delete_after__isnull=True,
    ).update(delete_after=delete_after)


@transaction.atomic
def close_verification_for_inactive_application(application, *, reason):
    """Stop uploads and start retention after withdrawal or screening rejection."""

    case = (
        VerificationCase.all_objects.select_for_update(of=("self",))
        .select_related("checklist")
        .filter(application=application)
        .first()
    )
    if not case:
        return None
    already_closed = case.completed_at is not None
    case.completed_at = case.completed_at or timezone.now()
    case.save(update_fields=("completed_at",))
    _schedule_evidence_deletion(case, now=case.completed_at)
    if not already_closed:
        _audit(
            case=case,
            event_type=f"verification.closed_{reason}",
            actor_type=VerificationAuditEvent.ActorType.SYSTEM,
            from_status=case.status,
            to_status=case.status,
            candidate_message="Verification closed because the application is no longer active.",
        )
    return case


@transaction.atomic
def submit_evidence(*, application, requirement_public_id, person_public_id, upload):
    application = (
        Application.all_objects.select_for_update(of=("self",))
        .select_related("person", "opening__venture")
        .get(pk=application.pk)
    )
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    if (
        application.person.public_id != person_public_id
        or application.withdrawn_at
        or application.candidate.canceled
        or not screening
        or screening.status != ScreeningDecision.Status.SHORTLISTED
    ):
        raise PermissionDenied
    requirement = (
        VerificationRequirement.all_objects.select_for_update(of=("self",))
        .select_related("case__checklist", "native_document")
        .filter(public_id=requirement_public_id, case__application=application)
        .first()
    )
    if not requirement or requirement.status not in {
        VerificationCase.Status.REQUESTED,
        VerificationCase.Status.REVISION_NEEDED,
    }:
        raise ValidationError(
            "This verification requirement is not accepting a document."
        )
    inspected = inspect_verification_document(
        upload,
        allowed_formats=requirement.allowed_formats,
        max_size_mb=requirement.max_size_mb,
    )
    result = scan_verification_upload(
        content=inspected.content,
        filename=Path(upload.name).name,
        content_type=inspected.content_type,
        sha256=inspected.sha256,
    )
    if result.status not in VerificationEvidence.ScanStatus.values:
        raise ValidationError("The malware scanner returned an unsupported result.")

    now = timezone.now()
    previous_evidence = requirement.evidence_versions.filter(
        superseded_at__isnull=True,
        deleted_at__isnull=True,
    ).first()
    if previous_evidence:
        previous_evidence.superseded_at = now
        previous_evidence.save(update_fields=("superseded_at",))
    latest = requirement.evidence_versions.order_by("-version_number").first()
    evidence = VerificationEvidence(
        requirement=requirement,
        version_number=(latest.version_number + 1 if latest else 1),
        original_name=Path(upload.name).name[:255],
        content_type=inspected.content_type,
        size_bytes=len(inspected.content),
        sha256=inspected.sha256,
        scan_status=result.status,
        scanned_at=(
            now if result.status != VerificationEvidence.ScanStatus.PENDING else None
        ),
        scan_reference=result.reference[:120],
    )
    evidence.file.save(
        evidence.original_name,
        ContentFile(inspected.content),
        save=False,
    )
    evidence.save()
    previous = requirement.status
    requirement.status = VerificationCase.Status.SUBMITTED
    requirement.submitted_at = now
    requirement.reviewed_at = None
    requirement.reviewed_by = None
    requirement.revision_reason = ""
    requirement.internal_note = ""
    requirement.native_document.status = "requested"
    requirement.native_document.reject_reason = ""
    requirement.native_document.save()
    requirement.save()
    _recalculate_case(requirement.case)
    _audit(
        case=requirement.case,
        requirement=requirement,
        event_type="verification.evidence_submitted",
        actor_type=VerificationAuditEvent.ActorType.CANDIDATE,
        actor_reference=person_public_id,
        from_status=previous,
        to_status=requirement.status,
        candidate_message="Document submitted securely for review.",
    )
    return evidence


@transaction.atomic
def review_requirement(*, user, requirement, decision, reason="", internal_note=""):
    requirement = (
        VerificationRequirement.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__venture",
            "native_document",
        )
        .get(pk=requirement.pk)
    )
    employee = require_application_access(
        user, requirement.case.application, "tvg_erasmus.review_verification"
    )
    application = requirement.case.application
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    if (
        application.withdrawn_at
        or application.candidate.canceled
        or not screening
        or screening.status != ScreeningDecision.Status.SHORTLISTED
    ):
        raise ValidationError("An inactive application cannot be reviewed.")
    evidence = requirement.evidence_versions.filter(
        superseded_at__isnull=True,
        deleted_at__isnull=True,
    ).first()
    if not evidence or evidence.scan_status != VerificationEvidence.ScanStatus.CLEAN:
        raise ValidationError("Evidence must pass malware scanning before review.")
    transitions = {
        VerificationCase.Status.SUBMITTED: {VerificationCase.Status.UNDER_REVIEW},
        VerificationCase.Status.UNDER_REVIEW: {
            VerificationCase.Status.VERIFIED,
            VerificationCase.Status.REVISION_NEEDED,
            VerificationCase.Status.REJECTED,
        },
    }
    if decision not in transitions.get(requirement.status, set()):
        raise ValidationError("This verification review transition is not allowed.")
    if (
        decision
        in {
            VerificationCase.Status.REVISION_NEEDED,
            VerificationCase.Status.REJECTED,
        }
        and not reason.strip()
    ):
        raise ValidationError("A candidate-facing reason is required.")

    previous = requirement.status
    requirement.status = decision
    requirement.reviewed_at = timezone.now()
    requirement.reviewed_by = employee
    requirement.revision_reason = reason.strip()[:500]
    requirement.internal_note = internal_note.strip()
    if decision == VerificationCase.Status.VERIFIED:
        requirement.native_document.status = "approved"
        requirement.native_document.reject_reason = ""
    elif decision in {
        VerificationCase.Status.REVISION_NEEDED,
        VerificationCase.Status.REJECTED,
    }:
        requirement.native_document.status = "rejected"
        requirement.native_document.reject_reason = requirement.revision_reason[:255]
    requirement.native_document.save()
    requirement.save()
    previous_case, current_case = _recalculate_case(requirement.case)
    candidate_message = {
        VerificationCase.Status.UNDER_REVIEW: "Your document is under review.",
        VerificationCase.Status.VERIFIED: "This verification requirement was approved.",
        VerificationCase.Status.REVISION_NEEDED: requirement.revision_reason,
        VerificationCase.Status.REJECTED: requirement.revision_reason,
    }[decision]
    _audit(
        case=requirement.case,
        requirement=requirement,
        event_type=f"verification.{decision}",
        actor_type=VerificationAuditEvent.ActorType.STAFF,
        actor_reference=None,
        from_status=previous,
        to_status=decision,
        candidate_message=candidate_message,
        internal_note=requirement.internal_note,
    )
    if decision in {
        VerificationCase.Status.REVISION_NEEDED,
        VerificationCase.Status.REJECTED,
        VerificationCase.Status.VERIFIED,
    }:
        _notification(
            case=requirement.case,
            requirement=requirement,
            event_type=f"verification.{decision}",
            idempotency_key=(
                f"verification-{decision}:{requirement.public_id}:"
                f"{evidence.version_number}"
            ),
            candidate_message=candidate_message,
            next_action=(
                "Upload a corrected document through your secure dashboard."
                if decision == VerificationCase.Status.REVISION_NEEDED
                else ""
            ),
        )
    if previous_case != current_case and current_case in {
        VerificationCase.Status.VERIFIED,
        VerificationCase.Status.REJECTED,
    }:
        requirement.case.reviewed_at = timezone.now()
        requirement.case.reviewed_by = employee
        requirement.case.save(update_fields=("reviewed_at", "reviewed_by"))
    return requirement


def interview_eligibility(application):
    if application.withdrawn_at:
        return InterviewEligibility(False, "the application was withdrawn")
    if application.candidate.canceled:
        return InterviewEligibility(False, "the application is not active")
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    if not screening or screening.status != ScreeningDecision.Status.SHORTLISTED:
        return InterviewEligibility(
            False, "initial HR screening has not shortlisted the application"
        )
    case = VerificationCase.all_objects.filter(application=application).first()
    if not case:
        return InterviewEligibility(False, "verification has not been requested")
    if case.status == VerificationCase.Status.NOT_REQUIRED:
        return InterviewEligibility(True, "verification is not required")
    required = VerificationRequirement.all_objects.filter(case=case, required=True)
    if (
        case.status == VerificationCase.Status.VERIFIED
        and required.exists()
        and not required.exclude(status=VerificationCase.Status.VERIFIED).exists()
    ):
        return InterviewEligibility(True, "all required verification was approved")
    return InterviewEligibility(False, "required verification is incomplete")


def validate_candidate_interview_eligibility(candidate):
    application = Application.all_objects.filter(candidate=candidate).first()
    if not application:
        return
    result = interview_eligibility(application)
    if not result.eligible:
        raise ValidationError(f"Interview blocked: {result.reason}.")


@transaction.atomic
def queue_requirement_reminder(*, user, requirement, now=None):
    requirement = (
        VerificationRequirement.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__venture",
        )
        .get(pk=requirement.pk)
    )
    require_application_access(
        user,
        requirement.case.application,
        "tvg_erasmus.send_verification_reminder",
    )
    return _queue_requirement_reminder(requirement=requirement, now=now)


def _queue_requirement_reminder(*, requirement, now=None):
    now = now or timezone.now()
    application = requirement.case.application
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    if (
        requirement.case.completed_at
        or application.withdrawn_at
        or application.candidate.canceled
        or not screening
        or screening.status != ScreeningDecision.Status.SHORTLISTED
    ):
        raise ValidationError(
            "The application is not accepting verification reminders."
        )
    if requirement.status not in {
        VerificationCase.Status.REQUESTED,
        VerificationCase.Status.REVISION_NEEDED,
    }:
        raise ValidationError("Only missing or corrected documents can be reminded.")
    notification = _notification(
        case=requirement.case,
        requirement=requirement,
        event_type="verification.reminder",
        idempotency_key=f"verification-reminder:{requirement.public_id}:{now.date().isoformat()}",
        candidate_message=f"Reminder: {requirement.title} is still required.",
        next_action="Upload the requested document through your secure dashboard.",
    )
    requirement.last_reminder_at = now
    requirement.save(update_fields=("last_reminder_at",))
    return notification


def queue_due_verification_reminders(*, now=None):
    now = now or timezone.now()
    queued = []
    requirements = VerificationRequirement.all_objects.filter(
        required=True,
        status__in=(
            VerificationCase.Status.REQUESTED,
            VerificationCase.Status.REVISION_NEEDED,
        ),
        case__due_at__lte=now,
        case__completed_at__isnull=True,
        case__is_active=True,
    ).select_related("case__checklist", "case__application__candidate")
    for requirement in requirements:
        interval = timedelta(days=requirement.case.checklist.reminder_interval_days)
        if (
            requirement.last_reminder_at
            and requirement.last_reminder_at + interval > now
        ):
            continue
        queued.append(_queue_requirement_reminder(requirement=requirement, now=now))
    return queued


@transaction.atomic
def purge_expired_verification_evidence(*, now=None):
    """Delete expired bytes while retaining minimal audit metadata."""

    now = now or timezone.now()
    evidence_rows = list(
        VerificationEvidence.all_objects.select_for_update(of=("self",)).filter(
            delete_after__lte=now,
            deleted_at__isnull=True,
        )
    )
    for evidence in evidence_rows:
        storage = evidence.file.storage
        stored_name = evidence.file.name
        if stored_name:
            storage.delete(stored_name)
        evidence.deleted_at = now
        evidence.save(update_fields=("deleted_at",))
    return len(evidence_rows)


def verification_projection(application):
    screening = ScreeningDecision.all_objects.filter(application=application).first()
    case = (
        VerificationCase.all_objects.filter(application=application)
        .prefetch_related("requirements", "notifications")
        .first()
    )
    if not case:
        return {
            "status": VerificationCase.Status.NOT_REQUESTED,
            "label": "Not requested",
            "interview_eligible": False,
            "next_action": (
                screening.applicant_message
                if screening
                and screening.status == ScreeningDecision.Status.INFORMATION_REQUESTED
                else "No verification action is currently required."
            ),
            "requirements": [],
            "notifications": [],
        }
    eligibility = interview_eligibility(application)
    accepting_uploads = bool(
        not application.withdrawn_at
        and not application.candidate.canceled
        and screening
        and screening.status == ScreeningDecision.Status.SHORTLISTED
    )
    next_action = {
        VerificationCase.Status.REQUESTED: "Upload every required document securely.",
        VerificationCase.Status.SUBMITTED: "Your documents are awaiting HR review.",
        VerificationCase.Status.UNDER_REVIEW: "HR is reviewing your documents.",
        VerificationCase.Status.REVISION_NEEDED: "Upload the corrected documents shown below.",
        VerificationCase.Status.VERIFIED: "Verification is complete. An interview may now be arranged.",
        VerificationCase.Status.REJECTED: "Verification could not be approved.",
        VerificationCase.Status.NOT_REQUIRED: "No verification documents are required.",
    }.get(case.status, "No verification action is currently required.")
    if not accepting_uploads:
        next_action = "This application is no longer accepting verification documents."
    return {
        "status": case.status,
        "label": case.get_status_display(),
        "interview_eligible": eligibility.eligible,
        "next_action": next_action,
        "due_at": case.due_at.isoformat() if case.due_at else None,
        "requirements": [
            {
                "id": str(item.public_id),
                "category": item.category,
                "title": item.title,
                "instructions": item.instructions,
                "required": item.required,
                "status": item.status,
                "status_label": item.get_status_display(),
                "allowed_formats": list(item.allowed_formats),
                "max_size_mb": item.max_size_mb,
                "revision_reason": item.revision_reason,
                "accepting_upload": accepting_uploads
                and item.status
                in {
                    VerificationCase.Status.REQUESTED,
                    VerificationCase.Status.REVISION_NEEDED,
                },
            }
            for item in case.requirements.all()
        ],
        "notifications": [
            {
                "id": str(item.event_id),
                "message": item.candidate_message,
                "next_action": item.next_action,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in case.notifications.all()
        ],
    }


def _arrangement_event(
    case,
    event_type,
    actor_type,
    *,
    employee=None,
    person_public_id=None,
    from_status="",
    to_status="",
    payload=None,
):
    return ErasmusCaseEvent.all_objects.create(
        case=case,
        event_type=event_type,
        actor_type=actor_type,
        actor_employee=employee,
        actor_person_public_id=person_public_id,
        from_status=from_status,
        to_status=to_status,
        payload=payload or {},
    )


def _calendar_months_after(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _duration_is_valid(start_date, end_date, minimum_months):
    return bool(
        start_date
        and end_date
        and end_date >= _calendar_months_after(start_date, minimum_months)
    )


def _policy_for_case(case, *, lock=False):
    queryset = ErasmusArrangementPolicy.objects
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    policy = queryset.filter(
        company=case.application.opening.venture.legal_employer
    ).first()
    if not policy:
        raise ValidationError(
            "Configure the legal employer's Erasmus arrangement policy first."
        )
    return policy


def _matching_approved_exception(case, policy):
    return DurationException.all_objects.filter(
        case=case,
        status=DurationException.Status.APPROVED,
        start_date=case.confirmed_start_date,
        end_date=case.confirmed_end_date,
        minimum_duration_months=policy.minimum_duration_months,
    ).first()


def _outstanding_requirements(case, policy):
    requirements = []
    if not case.candidate_confirmed_at or not (
        case.confirmed_start_date and case.confirmed_end_date
    ):
        requirements.append("Candidate must confirm the internship dates.")
    if (
        case.university_confirmation_status
        != ErasmusCase.UniversityConfirmation.CONFIRMED
    ):
        requirements.append("University arrangements must be confirmed.")
    if case.funding_declaration == ErasmusCase.FundingDeclaration.NOT_DECLARED:
        requirements.append("Erasmus+ funding or eligibility must be declared.")
    if not case.curriculum_reference.strip() or not case.curriculum_version.strip():
        requirements.append("Curriculum reference and version are required.")
    if (
        case.confirmed_start_date
        and case.confirmed_end_date
        and not _duration_is_valid(
            case.confirmed_start_date,
            case.confirmed_end_date,
            policy.minimum_duration_months,
        )
    ):
        if not _matching_approved_exception(case, policy):
            requirements.append(
                "The shorter duration requires an authorized exception."
            )
    return requirements


def visible_erasmus_cases(user):
    queryset = ErasmusCase.all_objects.select_related(
        "application__person",
        "application__candidate__stage_id",
        "application__opening__recruitment",
        "application__opening__venture",
        "selection_decision",
        "department",
        "supervisor",
    )
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_superuser:
        return queryset
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    company_ids = get_allowed_company_ids(user)
    if (
        not employee
        or not company_ids
        or not user.has_perm("tvg_erasmus.view_erasmuscase")
    ):
        return queryset.none()
    return (
        queryset.filter(
            application__opening__venture__legal_employer_id__in=company_ids
        )
        .filter(
            Q(application__opening__recruitment__recruitment_managers=employee)
            | Q(application__candidate__stage_id__stage_managers=employee)
            | Q(supervisor=employee)
        )
        .distinct()
    )


@transaction.atomic
def save_erasmus_case(*, user, application, selection_decision, case=None, **values):
    """Create or revise a proposal; approved arrangements cannot be edited."""

    application = (
        Application.all_objects.select_for_update(of=("self",))
        .select_related(
            "candidate__stage_id",
            "opening__recruitment",
            "opening__venture__legal_employer",
        )
        .get(pk=application.pk)
    )
    employee = require_application_access(
        user, application, "tvg_erasmus.manage_erasmus_case"
    )
    policy = ErasmusArrangementPolicy.objects.filter(
        company=application.opening.venture.legal_employer
    ).first()
    if not policy:
        raise ValidationError(
            "Configure the legal employer's Erasmus arrangement policy first."
        )
    if application.withdrawn_at or application.candidate.canceled:
        raise ValidationError("An inactive application cannot enter arrangements.")
    if (
        selection_decision.booking.invitation.application_id != application.pk
        or selection_decision.outcome != "selected"
    ):
        raise ValidationError("A selected human interview decision is required.")

    if case:
        case = ErasmusCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
        if case.application_id != application.pk or case.approved_at:
            raise ValidationError("These arrangements cannot be revised.")
        previous = case.arrangement_status
        proposal_changed = any(
            values.get(field) != getattr(case, field)
            for field in ("proposed_start_date", "proposed_end_date")
        )
        if (
            proposal_changed
            and DurationException.all_objects.filter(
                case=case,
                status=DurationException.Status.PENDING,
            ).exists()
        ):
            raise ValidationError(
                "Decide the pending duration exception before changing proposed dates."
            )
        for field, value in values.items():
            setattr(case, field, value)
        if proposal_changed and case.candidate_confirmed_at:
            case.confirmed_start_date = None
            case.confirmed_end_date = None
            case.internship_duration_days = 0
            case.candidate_confirmed_at = None
            case.arrangement_status = ErasmusCase.ArrangementStatus.PROPOSED
            case.acceptance_status = ErasmusCase.AcceptanceStatus.NOT_READY
        case.outstanding_requirements = _outstanding_requirements(case, policy)
        case.save()
        _arrangement_event(
            case,
            "erasmus.arrangements_revised",
            ErasmusCaseEvent.ActorType.STAFF,
            employee=employee,
            from_status=previous,
            to_status=case.arrangement_status,
            payload={"candidate_reconfirmation_required": proposal_changed},
        )
        return case

    if ErasmusCase.all_objects.filter(application=application).exists():
        raise ValidationError("An Erasmus case already exists for this application.")
    case = ErasmusCase(
        application=application,
        candidate=application.candidate,
        selection_decision=selection_decision,
        **values,
    )
    case.outstanding_requirements = _outstanding_requirements(case, policy)
    case.save()
    _arrangement_event(
        case,
        "erasmus.case_created",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        to_status=case.arrangement_status,
    )
    return case


@transaction.atomic
def confirm_arrangement_dates(*, case_id, person_public_id, start_date, end_date):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__person",
            "application__candidate",
            "application__opening__venture__legal_employer",
        )
        .filter(public_id=case_id, application__person__public_id=person_public_id)
        .first()
    )
    if not case:
        raise PermissionDenied
    if (
        case.approved_at
        or case.application.withdrawn_at
        or case.application.candidate.canceled
    ):
        raise ValidationError("These dates can no longer be changed.")
    if not start_date or not end_date or end_date < start_date:
        raise ValidationError("Provide a valid start date and end date.")
    dates_changed = bool(
        case.confirmed_start_date
        and case.confirmed_end_date
        and (
            case.confirmed_start_date != start_date
            or case.confirmed_end_date != end_date
        )
    )
    if (
        dates_changed
        and DurationException.all_objects.filter(
            case=case,
            status=DurationException.Status.PENDING,
        ).exists()
    ):
        raise ValidationError(
            "The pending duration exception must be decided before dates change."
        )
    overlap = ErasmusCase.all_objects.filter(
        application__person=case.application.person,
        confirmed_start_date__lte=end_date,
        confirmed_end_date__gte=start_date,
        arrangement_status__in=(
            ErasmusCase.ArrangementStatus.CANDIDATE_CONFIRMED,
            ErasmusCase.ArrangementStatus.HR_APPROVED,
        ),
        is_active=True,
    ).exclude(pk=case.pk)
    if overlap.exists():
        raise ValidationError(
            "These dates overlap another confirmed internship arrangement."
        )

    policy = _policy_for_case(case)
    previous = case.arrangement_status
    case.confirmed_start_date = start_date
    case.confirmed_end_date = end_date
    case.internship_duration_days = (end_date - start_date).days + 1
    case.candidate_confirmed_at = timezone.now()
    case.arrangement_status = ErasmusCase.ArrangementStatus.CANDIDATE_CONFIRMED
    case.acceptance_status = ErasmusCase.AcceptanceStatus.NOT_READY
    case.outstanding_requirements = _outstanding_requirements(case, policy)
    case.save()
    _arrangement_event(
        case,
        "erasmus.dates_confirmed",
        ErasmusCaseEvent.ActorType.CANDIDATE,
        person_public_id=person_public_id,
        from_status=previous,
        to_status=case.arrangement_status,
        payload={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    return case


@transaction.atomic
def request_duration_exception(*, user, case, reason):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__candidate__stage_id",
            "application__opening__venture__legal_employer",
        )
        .get(pk=case.pk)
    )
    employee = require_application_access(
        user, case.application, "tvg_erasmus.request_duration_exception"
    )
    if case.approved_at or not case.candidate_confirmed_at:
        raise ValidationError("Candidate-confirmed dates are required first.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("A written exception reason is required.")
    policy = _policy_for_case(case)
    if _duration_is_valid(
        case.confirmed_start_date,
        case.confirmed_end_date,
        policy.minimum_duration_months,
    ):
        raise ValidationError("These dates already satisfy the duration policy.")
    if DurationException.all_objects.filter(
        case=case, status=DurationException.Status.PENDING
    ).exists():
        raise ValidationError("A duration exception is already pending.")
    latest = (
        DurationException.all_objects.filter(case=case)
        .order_by("-version_number")
        .first()
    )
    exception = DurationException.all_objects.create(
        case=case,
        version_number=(latest.version_number + 1 if latest else 1),
        start_date=case.confirmed_start_date,
        end_date=case.confirmed_end_date,
        minimum_duration_months=policy.minimum_duration_months,
        reason=reason,
        requested_by=employee,
    )
    case.outstanding_requirements = _outstanding_requirements(case, policy)
    case.save(update_fields=("outstanding_requirements",))
    _arrangement_event(
        case,
        "erasmus.duration_exception_requested",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        payload={"exception_version": exception.version_number},
    )
    return exception


@transaction.atomic
def decide_duration_exception(*, user, exception, decision, note=""):
    exception = (
        DurationException.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__venture__legal_employer",
            "requested_by",
        )
        .get(pk=exception.pk)
    )
    employee = require_application_access(
        user,
        exception.case.application,
        "tvg_erasmus.approve_duration_exception",
    )
    if exception.status != DurationException.Status.PENDING:
        raise ValidationError("This exception has already been decided.")
    if decision not in {
        DurationException.Status.APPROVED,
        DurationException.Status.REJECTED,
    }:
        raise ValidationError("Choose approve or reject.")
    if employee.pk == exception.requested_by_id:
        raise ValidationError("The requester cannot authorize their own exception.")
    if decision == DurationException.Status.REJECTED and not note.strip():
        raise ValidationError("A rejection note is required.")
    case = exception.case
    if case.approved_at or (
        exception.start_date != case.confirmed_start_date
        or exception.end_date != case.confirmed_end_date
    ):
        raise ValidationError("The exception no longer matches the current dates.")
    exception.status = decision
    exception.decided_by = employee
    exception.decided_at = timezone.now()
    exception.decision_note = note.strip()
    exception.save()
    policy = _policy_for_case(case)
    case.outstanding_requirements = _outstanding_requirements(case, policy)
    case.save(update_fields=("outstanding_requirements",))
    _arrangement_event(
        case,
        f"erasmus.duration_exception_{decision}",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        payload={"exception_version": exception.version_number},
    )
    return exception


@transaction.atomic
def approve_acceptance_template(*, user, template):
    template = AcceptanceLetterTemplate.all_objects.select_for_update(of=("self",)).get(
        pk=template.pk
    )
    employee = _employee_for_user(user)
    if not user.is_superuser:
        if (
            template.company_id not in get_allowed_company_ids(user)
            or not user.has_perm("tvg_erasmus.approve_acceptance_template")
            or employee.get_company() != template.company
        ):
            raise PermissionDenied
    if template.approved_at:
        return template
    template.approved_by = employee
    template.approved_at = timezone.now()
    template.save()
    return template


@transaction.atomic
def approve_erasmus_case(*, user, case):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__candidate__stage_id",
            "application__opening__venture__legal_employer",
            "department",
            "supervisor",
        )
        .get(pk=case.pk)
    )
    employee = require_application_access(
        user, case.application, "tvg_erasmus.approve_erasmus_case"
    )
    if case.approved_at:
        raise ValidationError("These arrangements have already been approved.")
    if case.arrangement_status != ErasmusCase.ArrangementStatus.CANDIDATE_CONFIRMED:
        raise ValidationError("The candidate must confirm the dates first.")
    policy = _policy_for_case(case)
    case.outstanding_requirements = _outstanding_requirements(case, policy)
    if case.outstanding_requirements:
        case.save(update_fields=("outstanding_requirements",))
        raise ValidationError("Required arrangements are still outstanding.")
    now = timezone.now()
    case.arrangement_status = ErasmusCase.ArrangementStatus.HR_APPROVED
    case.acceptance_status = ErasmusCase.AcceptanceStatus.READY
    case.approved_by = employee
    case.approved_at = now
    duration_exception = _matching_approved_exception(case, policy)
    case.approval_snapshot = {
        "candidate_name": case.candidate.name,
        "role_title": case.application.opening.title,
        "venture_name": case.application.opening.venture.name,
        "university": case.university,
        "programme": case.programme,
        "funding_declaration": case.funding_declaration,
        "confirmed_start_date": case.confirmed_start_date.isoformat(),
        "confirmed_end_date": case.confirmed_end_date.isoformat(),
        "internship_duration_days": case.internship_duration_days,
        "working_model": case.working_model,
        "working_model_label": case.get_working_model_display(),
        "work_location": case.work_location,
        "department": case.department.department,
        "supervisor": case.supervisor.get_full_name(),
        "curriculum_reference": case.curriculum_reference,
        "curriculum_version": case.curriculum_version,
        "university_confirmation_status": case.university_confirmation_status,
        "minimum_duration_months": policy.minimum_duration_months,
        "duration_exception_version": (
            duration_exception.version_number if duration_exception else None
        ),
    }
    case.save()
    _arrangement_event(
        case,
        "erasmus.arrangements_approved",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        from_status=ErasmusCase.ArrangementStatus.CANDIDATE_CONFIRMED,
        to_status=case.arrangement_status,
        payload={"human_authorization": True},
    )
    return case


def _letter_context(case, template, letter_number, issue_date):
    snapshot = case.approval_snapshot
    required_snapshot_fields = {
        "candidate_name",
        "role_title",
        "venture_name",
        "university",
        "programme",
        "confirmed_start_date",
        "confirmed_end_date",
        "internship_duration_days",
        "working_model_label",
        "work_location",
        "department",
        "supervisor",
        "curriculum_reference",
        "curriculum_version",
    }
    if not isinstance(snapshot, dict) or required_snapshot_fields - snapshot.keys():
        raise ValidationError(
            "The HR approval snapshot is incomplete; do not invent document values."
        )
    return {
        "candidate_name": snapshot["candidate_name"],
        "role_title": snapshot["role_title"],
        "venture_name": snapshot["venture_name"],
        "university": snapshot["university"],
        "programme": snapshot["programme"],
        "confirmed_start_date": snapshot["confirmed_start_date"],
        "confirmed_end_date": snapshot["confirmed_end_date"],
        "internship_duration_days": snapshot["internship_duration_days"],
        "working_model": snapshot["working_model_label"],
        "work_location": snapshot["work_location"],
        "department": snapshot["department"],
        "supervisor": snapshot["supervisor"],
        "curriculum_reference": snapshot["curriculum_reference"],
        "curriculum_version": snapshot["curriculum_version"],
        "letter_number": letter_number,
        "issue_date": issue_date.isoformat(),
        "legal_entity_name": template.legal_entity_name,
        "legal_address": template.legal_address,
        "registration_details": template.registration_details,
        "authorized_signatory_name": template.authorized_signatory_name,
        "authorized_signatory_title": template.authorized_signatory_title,
    }


@transaction.atomic
def issue_acceptance_letter(*, user, case, template, issue_date=None):
    from tvg_erasmus.documents import render_acceptance_letter

    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__candidate__stage_id",
            "application__opening__venture__legal_employer",
            "application__opening__venture",
            "candidate",
            "department",
            "supervisor",
        )
        .get(pk=case.pk)
    )
    employee = require_application_access(
        user, case.application, "tvg_erasmus.issue_acceptance_letter"
    )
    if (
        case.arrangement_status != ErasmusCase.ArrangementStatus.HR_APPROVED
        or not case.approved_at
        or not case.approval_snapshot
    ):
        raise ValidationError("HR-approved arrangements are required before issue.")
    template = AcceptanceLetterTemplate.all_objects.select_for_update(of=("self",)).get(
        pk=template.pk
    )
    employer = case.application.opening.venture.legal_employer
    if (
        template.company_id != employer.pk
        or not template.approved_at
        or not template.approved_by_id
        or not template.is_active
    ):
        raise ValidationError("Use an active, approved legal-employer template.")
    policy = _policy_for_case(case, lock=True)
    prefix = policy.letter_number_prefix.strip()
    if not prefix or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}", prefix):
        raise ValidationError("Configure an approved letter-number prefix first.")
    issue_date = issue_date or timezone.localdate()
    sequence = policy.next_letter_sequence
    policy.next_letter_sequence += 1
    policy.save(update_fields=("next_letter_sequence",))
    letter_number = f"{prefix}/{issue_date.year}/{sequence:05d}"
    latest = (
        AcceptanceLetter.all_objects.filter(case=case)
        .order_by("-document_version")
        .first()
    )
    version = latest.document_version + 1 if latest else 1
    context = _letter_context(case, template, letter_number, issue_date)
    rendered = render_acceptance_letter(template=template, context=context)
    letter = AcceptanceLetter(
        case=case,
        template=template,
        document_version=version,
        letter_number=letter_number,
        issue_date=issue_date,
        rendered_text=rendered.rendered_text,
        sha256=rendered.sha256,
        size_bytes=len(rendered.content),
        authorized_by=employee,
        authorized_at=timezone.now(),
    )
    letter.pdf_file.save(
        f"acceptance-{letter.public_id}.pdf",
        ContentFile(rendered.content),
        save=False,
    )
    letter.save()
    previous = case.acceptance_status
    case.acceptance_status = ErasmusCase.AcceptanceStatus.ISSUED
    case.save(update_fields=("acceptance_status",))
    _arrangement_event(
        case,
        "erasmus.acceptance_letter_issued",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=case.acceptance_status,
        payload={
            "letter_id": str(letter.public_id),
            "document_version": version,
            "human_authorization": True,
        },
    )
    return letter


@transaction.atomic
def send_acceptance_letter(*, user, letter):
    letter = (
        AcceptanceLetter.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__venture",
        )
        .get(pk=letter.pk)
    )
    employee = require_application_access(
        user, letter.case.application, "tvg_erasmus.send_acceptance_letter"
    )
    if letter.status == AcceptanceLetter.Status.RECEIVED:
        return letter
    if letter.status != AcceptanceLetter.Status.ISSUED:
        raise ValidationError("Only an issued letter can be sent.")
    newer = AcceptanceLetter.all_objects.filter(
        case=letter.case, document_version__gt=letter.document_version
    ).exists()
    if newer:
        raise ValidationError("Only the latest document version can be sent.")
    now = timezone.now()
    letter.status = AcceptanceLetter.Status.SENT
    letter.sent_by = employee
    letter.sent_at = now
    letter.save(update_fields=("status", "sent_by", "sent_at"))
    case = letter.case
    previous = case.acceptance_status
    case.acceptance_status = ErasmusCase.AcceptanceStatus.SENT
    case.save(update_fields=("acceptance_status",))
    AcceptanceNotification.all_objects.get_or_create(
        idempotency_key=f"acceptance-sent:{letter.public_id}",
        defaults={
            "case": case,
            "letter": letter,
            "event_type": "erasmus.acceptance_letter_sent",
            "candidate_message": (
                "Your authorized acceptance letter is available securely in CareerHub."
            ),
        },
    )
    _arrangement_event(
        case,
        "erasmus.acceptance_letter_sent",
        ErasmusCaseEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=case.acceptance_status,
        payload={"letter_id": str(letter.public_id), "delivery": "outbox"},
    )
    return letter


@transaction.atomic
def confirm_acceptance_receipt(*, case_id, letter_id, person_public_id):
    letter = (
        AcceptanceLetter.all_objects.select_for_update(of=("self",))
        .select_related("case__application__person")
        .filter(
            public_id=letter_id,
            case__public_id=case_id,
            case__application__person__public_id=person_public_id,
        )
        .first()
    )
    if not letter:
        raise PermissionDenied
    if letter.status == AcceptanceLetter.Status.RECEIVED:
        return letter
    if letter.status != AcceptanceLetter.Status.SENT:
        raise ValidationError("The acceptance letter is not awaiting receipt.")
    if AcceptanceLetter.all_objects.filter(
        case=letter.case,
        document_version__gt=letter.document_version,
    ).exists():
        raise ValidationError("Only the latest document version can be acknowledged.")
    now = timezone.now()
    letter.status = AcceptanceLetter.Status.RECEIVED
    letter.received_at = now
    letter.received_by_person_public_id = person_public_id
    letter.save(
        update_fields=(
            "status",
            "received_at",
            "received_by_person_public_id",
        )
    )
    case = letter.case
    previous = case.acceptance_status
    case.acceptance_status = ErasmusCase.AcceptanceStatus.RECEIVED
    case.save(update_fields=("acceptance_status",))
    _arrangement_event(
        case,
        "erasmus.acceptance_receipt_confirmed",
        ErasmusCaseEvent.ActorType.CANDIDATE,
        person_public_id=person_public_id,
        from_status=previous,
        to_status=case.acceptance_status,
        payload={"letter_id": str(letter.public_id)},
    )
    return letter


def candidate_acceptance_letter(*, case_id, letter_id, person_public_id):
    letter = (
        AcceptanceLetter.all_objects.select_related("case__application__person")
        .filter(
            public_id=letter_id,
            case__public_id=case_id,
            case__application__person__public_id=person_public_id,
            status__in=(
                AcceptanceLetter.Status.SENT,
                AcceptanceLetter.Status.RECEIVED,
            ),
        )
        .first()
    )
    if not letter:
        raise PermissionDenied
    return letter


def arrangement_projection(application):
    case = (
        ErasmusCase.all_objects.filter(application=application)
        .prefetch_related("acceptance_letters", "acceptance_notifications")
        .first()
    )
    if not case:
        return None
    all_letters = list(case.acceptance_letters.all())
    latest_version = max(
        (letter.document_version for letter in all_letters),
        default=0,
    )
    letters = [
        {
            "id": str(letter.public_id),
            "letter_number": letter.letter_number,
            "version": letter.document_version,
            "issue_date": letter.issue_date.isoformat(),
            "status": letter.status,
            "status_label": letter.get_status_display(),
            "downloadable": letter.status
            in (AcceptanceLetter.Status.SENT, AcceptanceLetter.Status.RECEIVED),
            "can_confirm_receipt": (
                letter.status == AcceptanceLetter.Status.SENT
                and letter.document_version == latest_version
            ),
            "received_at": (
                letter.received_at.isoformat() if letter.received_at else None
            ),
        }
        for letter in all_letters
        if letter.status
        in (AcceptanceLetter.Status.SENT, AcceptanceLetter.Status.RECEIVED)
    ]
    return {
        "id": str(case.public_id),
        "arrangement_status": case.arrangement_status,
        "arrangement_status_label": case.get_arrangement_status_display(),
        "acceptance_status": case.acceptance_status,
        "acceptance_status_label": case.get_acceptance_status_display(),
        "university": case.university,
        "programme": case.programme,
        "proposed_start_date": case.proposed_start_date.isoformat(),
        "proposed_end_date": case.proposed_end_date.isoformat(),
        "confirmed_start_date": (
            case.confirmed_start_date.isoformat() if case.confirmed_start_date else None
        ),
        "confirmed_end_date": (
            case.confirmed_end_date.isoformat() if case.confirmed_end_date else None
        ),
        "internship_duration_days": case.internship_duration_days,
        "working_model": case.get_working_model_display(),
        "work_location": case.work_location,
        "university_confirmation_status": case.university_confirmation_status,
        "outstanding_requirements": list(case.outstanding_requirements),
        "can_confirm_dates": bool(
            not case.approved_at
            and not application.withdrawn_at
            and not application.candidate.canceled
        ),
        "letters": letters,
        "notifications": [
            {
                "id": str(item.event_id),
                "message": item.candidate_message,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in case.acceptance_notifications.all()
        ],
        "learning_agreement_ready": (
            case.acceptance_status == ErasmusCase.AcceptanceStatus.RECEIVED
        ),
    }
