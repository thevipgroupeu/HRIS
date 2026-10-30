from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from base.auth_backends import get_allowed_company_ids
from employee.models import Employee
from recruitment.models import InterviewSchedule
from tvg_careers.models import Application
from tvg_core.models import Person
from tvg_erasmus.services import interview_eligibility, require_application_access
from tvg_interviews.models import (
    COMPANY_TIME_ZONE,
    DecisionNotificationTemplate,
    InterviewBooking,
    InterviewDecision,
    InterviewEvent,
    InterviewFeedback,
    InterviewInvitation,
    InterviewNotification,
    InterviewSlot,
    InterviewType,
    validate_iana_timezone,
)
from tvg_interviews.providers import get_calendar_provider

ACTIVE_BOOKING_STATUSES = (InterviewBooking.Status.BOOKED,)


def _employee_for_user(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    if not employee:
        raise PermissionDenied
    return employee


def _has_company_access(user, application, permission):
    employee = _employee_for_user(user)
    company_id = application.opening.venture.legal_employer_id
    if user.is_superuser:
        return employee
    if company_id not in get_allowed_company_ids(user) or not user.has_perm(permission):
        raise PermissionDenied
    return employee


def _manager_access(user, application, permission):
    return require_application_access(user, application, permission)


def _feedback_access(user, booking):
    employee = _has_company_access(
        user,
        booking.invitation.application,
        "tvg_interviews.record_feedback",
    )
    if user.is_superuser:
        return employee
    invitation = booking.invitation
    assigned = invitation.interviewers.filter(pk=employee.pk).exists()
    manages = invitation.application.opening.recruitment.recruitment_managers.filter(
        pk=employee.pk
    ).exists()
    if invitation.application.candidate.stage_id_id:
        manages = (
            manages
            or invitation.application.candidate.stage_id.stage_managers.filter(
                pk=employee.pk
            ).exists()
        )
    if not assigned and not manages:
        raise PermissionDenied
    return employee


def visible_invitations(user):
    queryset = InterviewInvitation.all_objects.select_related(
        "application__candidate__stage_id",
        "application__opening__recruitment",
        "application__opening__venture",
        "interview_type",
    ).prefetch_related("interviewers", "slots")
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_superuser:
        return queryset
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    company_ids = get_allowed_company_ids(user)
    if (
        not employee
        or not company_ids
        or not user.has_perm("tvg_interviews.view_interviewinvitation")
    ):
        return queryset.none()
    return (
        queryset.filter(
            application__opening__venture__legal_employer_id__in=company_ids
        )
        .filter(
            Q(application__opening__recruitment__recruitment_managers=employee)
            | Q(application__candidate__stage_id__stage_managers=employee)
            | Q(interviewers=employee)
        )
        .distinct()
    )


def _event(
    invitation, event_type, actor_type, *, employee=None, person_id=None, payload=None
):
    return InterviewEvent.all_objects.create(
        invitation=invitation,
        event_type=event_type,
        actor_type=actor_type,
        actor_employee=employee,
        actor_person_public_id=person_id,
        payload=payload or {},
    )


def _notification(
    invitation,
    event_type,
    audience,
    idempotency_key,
    subject,
    message,
    *,
    employee=None,
    payload=None,
):
    notification, _created = InterviewNotification.all_objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "invitation": invitation,
            "event_type": event_type,
            "audience": audience,
            "recipient_employee": employee,
            "subject": subject,
            "message": message,
            "payload": payload or {},
        },
    )
    return notification


def _notify_staff(invitation, event_type, key_prefix, subject, message, payload=None):
    interviewers = list(invitation.interviewers.all())
    interviewer_ids = {employee.pk for employee in interviewers}
    for employee in interviewers:
        _notification(
            invitation,
            event_type,
            InterviewNotification.Audience.INTERVIEWER,
            f"{key_prefix}:interviewer:{employee.pk}",
            subject,
            message,
            employee=employee,
            payload=payload,
        )
    hr_recipients = list(
        invitation.application.opening.recruitment.recruitment_managers.exclude(
            pk__in=interviewer_ids
        )
    )
    if (
        invitation.invited_by_id not in interviewer_ids
        and invitation.invited_by_id not in {employee.pk for employee in hr_recipients}
    ):
        hr_recipients.append(invitation.invited_by)
    for employee in hr_recipients:
        _notification(
            invitation,
            event_type,
            InterviewNotification.Audience.HR,
            f"{key_prefix}:hr:{employee.pk}",
            subject,
            message,
            employee=employee,
            payload=payload,
        )


def _validate_timezone(value):
    try:
        validate_iana_timezone(value)
    except ValidationError:
        raise
    return ZoneInfo(value)


def _slot_payload(slot, candidate_timezone):
    company_zone = ZoneInfo(COMPANY_TIME_ZONE)
    candidate_zone = _validate_timezone(candidate_timezone)
    return {
        "slot_id": str(slot.public_id),
        "start_at": slot.start_at.isoformat(),
        "end_at": slot.end_at.isoformat(),
        "company_timezone": COMPANY_TIME_ZONE,
        "company_display": timezone.localtime(slot.start_at, company_zone).strftime(
            "%d %b %Y, %H:%M"
        ),
        "candidate_timezone": candidate_timezone,
        "candidate_display": timezone.localtime(slot.start_at, candidate_zone).strftime(
            "%d %b %Y, %H:%M"
        ),
    }


def _validate_interviewers(interview_type, interviewers):
    rows = list(interviewers)
    if not rows:
        raise ValidationError("Assign at least one interviewer.")
    company_id = interview_type.recruitment.company_id_id
    invalid = [
        employee
        for employee in rows
        if getattr(getattr(employee, "employee_work_info", None), "company_id_id", None)
        != company_id
    ]
    if invalid:
        raise ValidationError("Every interviewer must belong to the legal employer.")
    return rows


@transaction.atomic
def create_invitation(
    *,
    user,
    application,
    interview_type,
    interviewers,
    slot_starts,
    candidate_timezone=COMPANY_TIME_ZONE,
    expires_at=None,
):
    application = (
        Application.all_objects.select_for_update(of=("self",))
        .select_related(
            "candidate__stage_id", "opening__recruitment", "opening__venture"
        )
        .get(pk=application.pk)
    )
    employee = _manager_access(user, application, "tvg_interviews.invite_candidate")
    eligibility = interview_eligibility(application)
    if not eligibility.eligible:
        raise ValidationError(f"Interview invitation blocked: {eligibility.reason}.")
    if interview_type.recruitment_id != application.opening.recruitment_id:
        raise ValidationError("Interview type does not belong to this recruitment.")
    _validate_timezone(candidate_timezone)
    interviewer_rows = _validate_interviewers(interview_type, interviewers)
    starts = sorted(set(slot_starts))
    if not starts:
        raise ValidationError("Offer at least one interview slot.")
    now = timezone.now()
    if any(timezone.is_naive(start) or start <= now for start in starts):
        raise ValidationError("Every slot must be a future timezone-aware time.")
    try:
        invitation = InterviewInvitation.all_objects.create(
            application=application,
            interview_type=interview_type,
            candidate_timezone=candidate_timezone,
            instructions_snapshot=interview_type.instructions,
            provider_key_snapshot=interview_type.provider_key,
            invited_by=employee,
            expires_at=expires_at,
        )
    except IntegrityError as exc:
        raise ValidationError(
            "An active invitation already exists for this interview type."
        ) from exc
    invitation.interviewers.set(interviewer_rows)
    duration = timedelta(minutes=interview_type.duration_minutes)
    for start in starts:
        InterviewSlot.all_objects.create(
            invitation=invitation,
            start_at=start,
            end_at=start + duration,
        )
    _event(
        invitation,
        "interview.invited",
        InterviewEvent.ActorType.STAFF,
        employee=employee,
        payload={"slot_count": len(starts), "provider": interview_type.provider_key},
    )
    _notification(
        invitation,
        InterviewNotification.EventType.INVITATION,
        InterviewNotification.Audience.CANDIDATE,
        f"interview-invitation:{invitation.public_id}",
        "Choose your interview time",
        "Your verification is complete. Please choose one of the available interview times.",
        payload={"invitation_id": str(invitation.public_id)},
    )
    return invitation


def _active_conflicts(invitation, slot, *, exclude_booking=None):
    interviewer_ids = list(invitation.interviewers.values_list("pk", flat=True))
    bookings = InterviewBooking.all_objects.filter(
        status__in=ACTIVE_BOOKING_STATUSES,
        slot__start_at__lt=slot.end_at,
        slot__end_at__gt=slot.start_at,
    )
    if exclude_booking:
        bookings = bookings.exclude(pk=exclude_booking.pk)
    if bookings.filter(
        Q(invitation__interviewers__pk__in=interviewer_ids)
        | Q(invitation__application__person=invitation.application.person)
    ).exists():
        return True

    company_zone = ZoneInfo(COMPANY_TIME_ZONE)
    start_local = timezone.localtime(slot.start_at, company_zone)
    end_local = timezone.localtime(slot.end_at, company_zone)
    dates = {start_local.date(), end_local.date()}
    linked_ids = InterviewBooking.all_objects.values_list(
        "native_schedule_id", flat=True
    )
    native = (
        InterviewSchedule._base_manager.filter(
            interview_date__in=dates,
            employee_id__pk__in=interviewer_ids,
        )
        .exclude(pk__in=linked_ids)
        .distinct()
    )
    for schedule in native:
        existing_start = timezone.make_aware(
            datetime.combine(schedule.interview_date, schedule.interview_time),
            company_zone,
        )
        existing_end = existing_start + timedelta(
            minutes=invitation.interview_type.duration_minutes
        )
        if existing_start < slot.end_at and existing_end > slot.start_at:
            return True
    return False


def _lock_booking_resources(invitation):
    """Serialize bookings that share a candidate identity or interviewer."""

    interviewer_ids = list(invitation.interviewers.values_list("pk", flat=True))
    list(
        Employee._base_manager.select_for_update(of=("self",))
        .filter(pk__in=interviewer_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    list(
        Person._base_manager.select_for_update(of=("self",))
        .filter(pk=invitation.application.person_id)
        .values_list("pk", flat=True)
    )


def _native_values(invitation, slot):
    local_start = timezone.localtime(slot.start_at, ZoneInfo(COMPANY_TIME_ZONE))
    return local_start.date(), local_start.time().replace(tzinfo=None)


@transaction.atomic
def book_interview(*, invitation_id, slot_id, person_public_id, candidate_timezone):
    invitation = (
        InterviewInvitation.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__person",
            "application__candidate__stage_id",
            "application__opening__venture",
            "interview_type__interview_stage",
        )
        .prefetch_related("interviewers")
        .filter(
            public_id=invitation_id, application__person__public_id=person_public_id
        )
        .first()
    )
    if not invitation:
        raise PermissionDenied
    _validate_timezone(candidate_timezone)
    if invitation.status != InterviewInvitation.Status.INVITED:
        raise ValidationError("This invitation is not accepting a booking.")
    if invitation.expires_at and invitation.expires_at <= timezone.now():
        invitation.status = InterviewInvitation.Status.EXPIRED
        invitation.save(update_fields=("status",))
        raise ValidationError("This invitation has expired.")
    eligibility = interview_eligibility(invitation.application)
    if not eligibility.eligible:
        raise ValidationError("Verification is no longer complete.")
    slot = (
        InterviewSlot.all_objects.select_for_update(of=("self",))
        .filter(
            public_id=slot_id,
            invitation=invitation,
            status=InterviewSlot.Status.AVAILABLE,
        )
        .first()
    )
    if not slot or slot.start_at <= timezone.now():
        raise ValidationError("This slot is no longer available.")
    _lock_booking_resources(invitation)
    if _active_conflicts(invitation, slot):
        raise ValidationError("This slot conflicts with another interview.")

    provider = get_calendar_provider(invitation.provider_key_snapshot)
    provider_result = provider.book(
        invitation=invitation,
        slot=slot,
        idempotency_key=f"book:{invitation.public_id}:{slot.public_id}",
    )
    date_value, time_value = _native_values(invitation, slot)
    schedule = InterviewSchedule._base_manager.create(
        candidate_id=invitation.application.candidate,
        interview_date=date_value,
        interview_time=time_value,
        description=f"{invitation.interview_type.name} ({invitation.provider_key_snapshot})",
    )
    schedule.employee_id.set(invitation.interviewers.all())
    booking = InterviewBooking.all_objects.create(
        invitation=invitation,
        slot=slot,
        native_schedule=schedule,
        candidate_timezone=candidate_timezone,
        meeting_link=provider_result.meeting_link,
        provider_booking_reference=provider_result.reference,
    )
    slot.status = InterviewSlot.Status.BOOKED
    slot.save(update_fields=("status",))
    invitation.status = InterviewInvitation.Status.BOOKED
    invitation.candidate_timezone = candidate_timezone
    invitation.save(update_fields=("status", "candidate_timezone"))
    candidate = invitation.application.candidate
    candidate.stage_id = invitation.interview_type.interview_stage
    candidate.save()

    payload = _slot_payload(slot, candidate_timezone)
    _event(
        invitation,
        "interview.booked",
        InterviewEvent.ActorType.CANDIDATE,
        person_id=person_public_id,
        payload=payload,
    )
    key = f"interview-booked:{booking.public_id}"
    _notification(
        invitation,
        InterviewNotification.EventType.BOOKING_CONFIRMATION,
        InterviewNotification.Audience.CANDIDATE,
        f"{key}:candidate",
        "Interview booking confirmed",
        "Your interview time is confirmed. Times are shown in both your timezone and Europe/Warsaw.",
        payload=payload,
    )
    _notify_staff(
        invitation,
        InterviewNotification.EventType.BOOKING_CONFIRMATION,
        key,
        "Interview booking confirmed",
        "A candidate has booked an assigned interview slot.",
        payload=payload,
    )
    return booking


@transaction.atomic
def reschedule_interview(
    *,
    invitation_id,
    slot_id,
    person_public_id=None,
    user=None,
    candidate_timezone=None,
    override_notice=False,
):
    invitation = (
        InterviewInvitation.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__person",
            "application__candidate__stage_id",
            "application__opening__venture",
            "interview_type",
            "booking__slot",
            "booking__native_schedule",
        )
        .prefetch_related("interviewers")
        .filter(public_id=invitation_id)
        .first()
    )
    if not invitation or invitation.status != InterviewInvitation.Status.BOOKED:
        raise ValidationError("This interview cannot be rescheduled.")
    booking = invitation.booking
    employee = None
    actor_type = InterviewEvent.ActorType.CANDIDATE
    if user is not None:
        employee = _manager_access(
            user, invitation.application, "tvg_interviews.manage_interview"
        )
        actor_type = InterviewEvent.ActorType.STAFF
        if override_notice and not user.has_perm(
            "tvg_interviews.override_reschedule_policy"
        ):
            raise PermissionDenied
    elif invitation.application.person.public_id != person_public_id:
        raise PermissionDenied
    elif (
        booking.candidate_reschedule_count
        >= invitation.interview_type.candidate_reschedule_limit
    ):
        raise ValidationError("The candidate reschedule allowance has been used.")

    notice_deadline = booking.slot.start_at - timedelta(
        hours=invitation.interview_type.reschedule_notice_hours
    )
    if timezone.now() > notice_deadline and not override_notice:
        raise ValidationError(
            f"Rescheduling requires at least {invitation.interview_type.reschedule_notice_hours} hours' notice."
        )
    slot = (
        InterviewSlot.all_objects.select_for_update(of=("self",))
        .filter(
            public_id=slot_id,
            invitation=invitation,
            status=InterviewSlot.Status.AVAILABLE,
        )
        .first()
    )
    if not slot or slot.start_at <= timezone.now():
        raise ValidationError("The requested replacement slot is unavailable.")
    _lock_booking_resources(invitation)
    if _active_conflicts(invitation, slot, exclude_booking=booking):
        raise ValidationError("The replacement slot conflicts with another interview.")
    candidate_timezone = candidate_timezone or booking.candidate_timezone
    _validate_timezone(candidate_timezone)
    provider = get_calendar_provider(invitation.provider_key_snapshot)
    result = provider.reschedule(
        booking=booking,
        slot=slot,
        idempotency_key=(
            f"reschedule:{booking.public_id}:{booking.candidate_reschedule_count + 1}:{slot.public_id}"
        ),
    )
    old_slot = booking.slot
    old_slot.status = InterviewSlot.Status.AVAILABLE
    old_slot.save(update_fields=("status",))
    slot.status = InterviewSlot.Status.BOOKED
    slot.save(update_fields=("status",))
    booking.slot = slot
    booking.candidate_timezone = candidate_timezone
    booking.meeting_link = result.meeting_link
    booking.provider_booking_reference = result.reference
    booking.rescheduled_at = timezone.now()
    if user is None:
        booking.candidate_reschedule_count += 1
    booking.save(
        update_fields=(
            "slot",
            "candidate_timezone",
            "meeting_link",
            "provider_booking_reference",
            "rescheduled_at",
            "candidate_reschedule_count",
        )
    )
    date_value, time_value = _native_values(invitation, slot)
    schedule = booking.native_schedule
    schedule.interview_date = date_value
    schedule.interview_time = time_value
    schedule.save(update_fields=("interview_date", "interview_time"))
    invitation.candidate_timezone = candidate_timezone
    invitation.save(update_fields=("candidate_timezone",))

    payload = {
        "previous_slot_id": str(old_slot.public_id),
        **_slot_payload(slot, candidate_timezone),
        "hr_override": bool(override_notice),
    }
    _event(
        invitation,
        "interview.rescheduled",
        actor_type,
        employee=employee,
        person_id=person_public_id,
        payload=payload,
    )
    key = f"interview-rescheduled:{booking.public_id}:{slot.public_id}"
    _notification(
        invitation,
        InterviewNotification.EventType.RESCHEDULE_CONFIRMATION,
        InterviewNotification.Audience.CANDIDATE,
        f"{key}:candidate",
        "Interview rescheduled",
        "Your new interview time is confirmed.",
        payload=payload,
    )
    _notify_staff(
        invitation,
        InterviewNotification.EventType.RESCHEDULE_CONFIRMATION,
        key,
        "Interview rescheduled",
        "An assigned interview has been rescheduled.",
        payload=payload,
    )
    return booking


@transaction.atomic
def complete_interview(*, user, booking):
    booking = (
        InterviewBooking.all_objects.select_for_update(of=("self",))
        .select_related(
            "invitation__application__candidate__stage_id",
            "invitation__application__opening__venture",
            "native_schedule",
        )
        .get(pk=booking.pk)
    )
    employee = _manager_access(
        user, booking.invitation.application, "tvg_interviews.manage_interview"
    )
    if booking.status != InterviewBooking.Status.BOOKED:
        raise ValidationError("Only a booked interview can be completed.")
    now = timezone.now()
    booking.status = InterviewBooking.Status.COMPLETED
    booking.completed_at = now
    booking.save(update_fields=("status", "completed_at"))
    booking.native_schedule.completed = True
    booking.native_schedule.save(update_fields=("completed",))
    invitation = booking.invitation
    invitation.status = InterviewInvitation.Status.COMPLETED
    invitation.save(update_fields=("status",))
    _event(
        invitation,
        "interview.completed",
        InterviewEvent.ActorType.STAFF,
        employee=employee,
    )
    return booking


@transaction.atomic
def record_feedback(
    *,
    user,
    booking,
    competency_ratings,
    strengths="",
    concerns="",
    private_notes="",
    recommendation="none",
):
    booking = (
        InterviewBooking.all_objects.select_for_update(of=("self",))
        .select_related(
            "invitation__application__candidate__stage_id",
            "invitation__application__opening__venture",
        )
        .prefetch_related("invitation__interviewers")
        .get(pk=booking.pk)
    )
    employee = _feedback_access(user, booking)
    if booking.status != InterviewBooking.Status.COMPLETED:
        raise ValidationError("Feedback can be submitted only after completion.")
    if InterviewFeedback.all_objects.filter(
        booking=booking,
        interviewer=employee,
    ).exists():
        raise ValidationError("Feedback has already been submitted and is immutable.")
    feedback = InterviewFeedback(
        booking=booking,
        interviewer=employee,
        competency_ratings=competency_ratings,
        strengths=strengths.strip(),
        concerns=concerns.strip(),
        private_notes=private_notes.strip(),
        recommendation=recommendation,
    )
    feedback.full_clean()
    feedback.save()
    _event(
        booking.invitation,
        "interview.feedback_submitted",
        InterviewEvent.ActorType.STAFF,
        employee=employee,
        payload={"recommendation": recommendation},
    )
    return feedback


def _render_template(template, application):
    context = {
        "candidate_name": application.candidate.name,
        "role_title": application.opening.title,
        "venture_name": application.opening.venture.name,
    }
    try:
        return template.subject.format_map(context), template.body.format_map(context)
    except (KeyError, ValueError) as exc:
        raise ValidationError(
            "The approved decision template contains an invalid placeholder."
        ) from exc


@transaction.atomic
def record_decision(*, user, booking, outcome, template=None, internal_reason=""):
    booking = (
        InterviewBooking.all_objects.select_for_update(of=("self",))
        .select_related(
            "invitation__application__candidate__stage_id",
            "invitation__application__opening__recruitment",
            "invitation__application__opening__venture",
            "invitation__interview_type__selected_stage",
            "invitation__interview_type__rejected_stage",
            "invitation__interview_type__hold_stage",
        )
        .get(pk=booking.pk)
    )
    application = booking.invitation.application
    employee = _manager_access(user, application, "tvg_interviews.record_decision")
    if booking.status != InterviewBooking.Status.COMPLETED:
        raise ValidationError("Complete the interview before recording a decision.")
    if InterviewDecision.all_objects.filter(booking=booking).exists():
        raise ValidationError("A human decision has already been recorded.")
    if outcome not in DecisionNotificationTemplate.Outcome.values:
        raise ValidationError("Unsupported interview decision.")
    if outcome == DecisionNotificationTemplate.Outcome.REJECTED:
        if (
            not template
            or not template.approved
            or not template.approved_by_id
            or not template.approved_at
            or template.outcome != outcome
            or template.recruitment_id != application.opening.recruitment_id
        ):
            raise ValidationError(
                "Rejection requires an approved matching notification template."
            )
    if template:
        if (
            not template.approved
            or not template.approved_by_id
            or not template.approved_at
            or template.outcome != outcome
        ):
            raise ValidationError("Use an approved template for this decision outcome.")
        subject, message = _render_template(template, application)
    else:
        subject = "Your THEVIPGROUP interview outcome"
        message = {
            DecisionNotificationTemplate.Outcome.SELECTED: "You have been selected following your interview. HR will contact you with the next human-led step.",
            DecisionNotificationTemplate.Outcome.HOLD: "Your application is currently on hold following your interview. We will update you when the review continues.",
        }[outcome]

    interview_type = booking.invitation.interview_type
    stage = {
        DecisionNotificationTemplate.Outcome.SELECTED: interview_type.selected_stage,
        DecisionNotificationTemplate.Outcome.REJECTED: interview_type.rejected_stage,
        DecisionNotificationTemplate.Outcome.HOLD: interview_type.hold_stage,
    }[outcome]
    candidate = application.candidate
    candidate.canceled = outcome == DecisionNotificationTemplate.Outcome.REJECTED
    candidate.stage_id = stage
    candidate.save()
    decision = InterviewDecision.all_objects.create(
        booking=booking,
        outcome=outcome,
        decided_by=employee,
        template=template,
        notification_subject=subject,
        notification_message=message,
        internal_reason=internal_reason.strip(),
        resulting_stage=stage,
    )
    _event(
        booking.invitation,
        "interview.decision_recorded",
        InterviewEvent.ActorType.STAFF,
        employee=employee,
        payload={"outcome": outcome, "stage_id": stage.pk, "human_decision": True},
    )
    _notification(
        booking.invitation,
        InterviewNotification.EventType.DECISION,
        InterviewNotification.Audience.CANDIDATE,
        f"interview-decision:{decision.public_id}",
        subject,
        message,
        payload={"outcome": outcome},
    )
    return decision


@transaction.atomic
def cancel_interview(*, user, booking, reason=""):
    booking = (
        InterviewBooking.all_objects.select_for_update(of=("self",))
        .select_related(
            "invitation__application__candidate__stage_id",
            "invitation__application__opening__venture",
            "invitation__interview_type",
            "slot",
            "native_schedule",
        )
        .get(pk=booking.pk)
    )
    employee = _manager_access(
        user, booking.invitation.application, "tvg_interviews.manage_interview"
    )
    if booking.status != InterviewBooking.Status.BOOKED:
        raise ValidationError("Only a booked interview can be cancelled.")
    provider = get_calendar_provider(booking.invitation.provider_key_snapshot)
    provider.cancel(
        booking=booking,
        idempotency_key=f"cancel:{booking.public_id}",
    )
    now = timezone.now()
    booking.status = InterviewBooking.Status.CANCELLED
    booking.cancelled_at = now
    booking.save(update_fields=("status", "cancelled_at"))
    booking.slot.status = InterviewSlot.Status.CANCELLED
    booking.slot.save(update_fields=("status",))
    invitation = booking.invitation
    invitation.status = InterviewInvitation.Status.CANCELLED
    invitation.cancelled_at = now
    invitation.save(update_fields=("status", "cancelled_at"))
    booking.native_schedule.description = (
        f"[Cancelled] {booking.native_schedule.description}"
    )[:255]
    booking.native_schedule.save(update_fields=("description",))
    _event(
        invitation,
        "interview.cancelled",
        InterviewEvent.ActorType.STAFF,
        employee=employee,
        payload={"reason_recorded": bool(reason.strip())},
    )
    key = f"interview-cancelled:{booking.public_id}"
    _notification(
        invitation,
        InterviewNotification.EventType.CANCELLATION,
        InterviewNotification.Audience.CANDIDATE,
        f"{key}:candidate",
        "Interview cancelled",
        "Your interview has been cancelled. HR will contact you if another step is required.",
    )
    _notify_staff(
        invitation,
        InterviewNotification.EventType.CANCELLATION,
        key,
        "Interview cancelled",
        "An assigned interview has been cancelled.",
    )
    return booking


@transaction.atomic
def queue_interview_reminder(*, booking, now=None):
    booking = (
        InterviewBooking.all_objects.select_for_update(of=("self",))
        .select_related("invitation", "slot")
        .get(pk=booking.pk)
    )
    now = now or timezone.now()
    if booking.status != InterviewBooking.Status.BOOKED or booking.slot.start_at <= now:
        raise ValidationError("Only a future booked interview can be reminded.")
    payload = _slot_payload(booking.slot, booking.candidate_timezone)
    key = f"interview-reminder:{booking.public_id}:{now.date().isoformat()}"
    candidate = _notification(
        booking.invitation,
        InterviewNotification.EventType.REMINDER,
        InterviewNotification.Audience.CANDIDATE,
        f"{key}:candidate",
        "Interview reminder",
        "Reminder: your interview is approaching.",
        payload=payload,
    )
    _notify_staff(
        booking.invitation,
        InterviewNotification.EventType.REMINDER,
        key,
        "Interview reminder",
        "Reminder: an assigned interview is approaching.",
        payload=payload,
    )
    return candidate


def interview_projection(application, *, candidate_timezone=None):
    invitations = (
        InterviewInvitation.all_objects.filter(application=application)
        .select_related("interview_type")
        .prefetch_related("slots", "notifications")
        .order_by("invited_at")
    )
    results = []
    for invitation in invitations:
        timezone_name = candidate_timezone or invitation.candidate_timezone
        try:
            _validate_timezone(timezone_name)
        except ValidationError:
            timezone_name = invitation.candidate_timezone
        booking = getattr(invitation, "booking", None)
        available_slots = [
            _slot_payload(slot, timezone_name)
            for slot in invitation.slots.all()
            if slot.status == InterviewSlot.Status.AVAILABLE
            and slot.start_at > timezone.now()
        ]
        booking_data = None
        if booking:
            booking_data = {
                **_slot_payload(booking.slot, booking.candidate_timezone),
                "status": booking.status,
                "meeting_link": (
                    booking.meeting_link
                    if booking.status == InterviewBooking.Status.BOOKED
                    else ""
                ),
                "reschedule_count": booking.candidate_reschedule_count,
                "reschedule_limit": invitation.interview_type.candidate_reschedule_limit,
                "can_reschedule": bool(
                    booking.status == InterviewBooking.Status.BOOKED
                    and booking.candidate_reschedule_count
                    < invitation.interview_type.candidate_reschedule_limit
                    and timezone.now()
                    <= booking.slot.start_at
                    - timedelta(hours=invitation.interview_type.reschedule_notice_hours)
                ),
                "reschedule_notice_hours": invitation.interview_type.reschedule_notice_hours,
            }
        candidate_notifications = [
            {
                "id": str(item.event_id),
                "type": item.event_type,
                "subject": item.subject,
                "message": item.message,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in invitation.notifications.all()
            if item.audience == InterviewNotification.Audience.CANDIDATE
        ]
        results.append(
            {
                "id": str(invitation.public_id),
                "type": invitation.interview_type.name,
                "location_mode": invitation.interview_type.location_mode,
                "status": invitation.status,
                "company_timezone": COMPANY_TIME_ZONE,
                "candidate_timezone": timezone_name,
                "instructions": invitation.instructions_snapshot,
                "available_slots": available_slots,
                "booking": booking_data,
                "notifications": candidate_notifications,
            }
        )
    return results
