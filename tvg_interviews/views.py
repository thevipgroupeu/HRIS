from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from tvg_careers.models import Application
from tvg_erasmus.services import visible_applications
from tvg_interviews.forms import (
    DecisionForm,
    FeedbackForm,
    InvitationForm,
    StaffRescheduleForm,
)
from tvg_interviews.models import DecisionNotificationTemplate, InterviewBooking
from tvg_interviews.services import (
    cancel_interview,
    complete_interview,
    create_invitation,
    record_decision,
    record_feedback,
    reschedule_interview,
    visible_invitations,
)


def _message_error(request, exc):
    messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))


@login_required
@require_GET
def interview_dashboard(request):
    applications = visible_applications(request.user).select_related(
        "verification_case", "opening__recruitment"
    )
    invitations = visible_invitations(request.user)
    decision_templates = DecisionNotificationTemplate.all_objects.filter(
        approved=True,
        is_active=True,
    )
    return render(
        request,
        "tvg_interviews/dashboard.html",
        {
            "applications": applications,
            "invitations": invitations,
            "decision_templates": decision_templates,
            "can_view_feedback": bool(
                request.user.is_superuser
                or request.user.has_perm("tvg_interviews.record_feedback")
                or request.user.has_perm("tvg_interviews.record_decision")
                or request.user.has_perm("tvg_interviews.manage_interview")
            ),
        },
    )


@login_required
def invite_candidate(request, application_id):
    application = get_object_or_404(
        visible_applications(request.user), public_id=application_id
    )
    form = InvitationForm(
        request.POST or None,
        application=application,
    )
    if request.method == "POST" and form.is_valid():
        try:
            create_invitation(
                user=request.user,
                application=application,
                interview_type=form.cleaned_data["interview_type"],
                interviewers=form.cleaned_data["interviewers"],
                slot_starts=form.cleaned_data["slot_starts"],
                candidate_timezone=form.cleaned_data["candidate_timezone"],
                expires_at=form.cleaned_data["expires_at"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _message_error(request, exc)
        else:
            messages.success(
                request, "Interview invitation event queued; no email was sent."
            )
            return redirect("tvg_interviews:dashboard")
    return render(
        request,
        "tvg_interviews/invite.html",
        {"application": application, "form": form},
    )


def _booking_for_user(request, booking_id):
    return get_object_or_404(
        InterviewBooking.all_objects.select_related(
            "invitation__application__candidate__stage_id",
            "invitation__application__opening__venture",
            "invitation__application__opening__recruitment",
            "invitation__interview_type",
        ),
        public_id=booking_id,
    )


@login_required
@require_POST
def complete_booking(request, booking_id):
    booking = _booking_for_user(request, booking_id)
    try:
        complete_interview(user=request.user, booking=booking)
    except ValidationError as exc:
        _message_error(request, exc)
    else:
        messages.success(request, "Interview marked complete.")
    return redirect("tvg_interviews:dashboard")


@login_required
@require_POST
def submit_feedback(request, booking_id):
    booking = _booking_for_user(request, booking_id)
    form = FeedbackForm(request.POST)
    if form.is_valid():
        try:
            record_feedback(user=request.user, booking=booking, **form.cleaned_data)
        except ValidationError as exc:
            _message_error(request, exc)
        else:
            messages.success(request, "Private structured feedback recorded.")
    else:
        messages.error(request, "The feedback was not valid.")
    return redirect("tvg_interviews:dashboard")


@login_required
@require_POST
def submit_decision(request, booking_id):
    booking = _booking_for_user(request, booking_id)
    form = DecisionForm(
        request.POST,
        recruitment=booking.invitation.application.opening.recruitment,
    )
    if form.is_valid():
        try:
            record_decision(user=request.user, booking=booking, **form.cleaned_data)
        except ValidationError as exc:
            _message_error(request, exc)
        else:
            messages.success(request, "Human recruitment decision recorded.")
    else:
        messages.error(request, "The recruitment decision was not valid.")
    return redirect("tvg_interviews:dashboard")


@login_required
@require_POST
def staff_reschedule(request, booking_id):
    booking = _booking_for_user(request, booking_id)
    form = StaffRescheduleForm(request.POST, invitation=booking.invitation)
    if form.is_valid():
        try:
            reschedule_interview(
                user=request.user,
                invitation_id=booking.invitation.public_id,
                slot_id=form.cleaned_data["slot"].public_id,
                override_notice=form.cleaned_data["override_notice"],
            )
        except ValidationError as exc:
            _message_error(request, exc)
        else:
            messages.success(request, "Interview rescheduled.")
    else:
        messages.error(request, "The reschedule request was not valid.")
    return redirect("tvg_interviews:dashboard")


@login_required
@require_POST
def cancel_booking(request, booking_id):
    booking = _booking_for_user(request, booking_id)
    try:
        cancel_interview(
            user=request.user, booking=booking, reason=request.POST.get("reason", "")
        )
    except ValidationError as exc:
        _message_error(request, exc)
    else:
        messages.success(request, "Interview cancelled and notification events queued.")
    return redirect("tvg_interviews:dashboard")
