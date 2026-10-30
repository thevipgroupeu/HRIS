from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from tvg_erasmus.forms import (
    AcceptanceLetterIssueForm,
    DurationExceptionDecisionForm,
    DurationExceptionRequestForm,
    ErasmusCaseForm,
    ScreeningActionForm,
    VerificationReviewForm,
)
from tvg_erasmus.models import (
    AcceptanceLetter,
    AcceptanceLetterTemplate,
    DurationException,
    ErasmusCase,
    VerificationEvidence,
    VerificationRequirement,
)
from tvg_erasmus.services import (
    approve_acceptance_template,
    approve_erasmus_case,
    decide_duration_exception,
    issue_acceptance_letter,
    queue_requirement_reminder,
    request_duration_exception,
    require_application_access,
    review_requirement,
    save_erasmus_case,
    screen_application,
    send_acceptance_letter,
    visible_applications,
    visible_erasmus_cases,
)
from tvg_interviews.models import InterviewDecision


@login_required
@require_GET
def screening_dashboard(request):
    applications = (
        visible_applications(request.user)
        .select_related(
            "screening",
            "verification_case",
        )
        .prefetch_related(
            "verification_case__requirements__evidence_versions",
        )
    )
    return render(
        request,
        "tvg_erasmus/screening_dashboard.html",
        {
            "applications": applications,
            "screening_form": ScreeningActionForm(),
            "review_form": VerificationReviewForm(),
        },
    )


@login_required
@require_POST
def screening_action(request, application_id):
    application = get_object_or_404(
        visible_applications(request.user), public_id=application_id
    )
    form = ScreeningActionForm(request.POST)
    if form.is_valid():
        try:
            screen_application(
                user=request.user,
                application=application,
                action=form.cleaned_data["action"],
                applicant_message=form.cleaned_data["applicant_message"],
                internal_note=form.cleaned_data["internal_note"],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Screening decision recorded.")
    else:
        messages.error(request, "The screening action was not valid.")
    return redirect("tvg_erasmus:screening-dashboard")


@login_required
@require_POST
def requirement_review(request, requirement_id):
    requirement = get_object_or_404(
        VerificationRequirement.all_objects.select_related(
            "case__application__opening__venture"
        ),
        public_id=requirement_id,
    )
    form = VerificationReviewForm(request.POST)
    if form.is_valid():
        try:
            review_requirement(
                user=request.user,
                requirement=requirement,
                decision=form.cleaned_data["decision"],
                reason=form.cleaned_data["reason"],
                internal_note=form.cleaned_data["internal_note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Verification review recorded.")
    else:
        messages.error(request, "The verification review was not valid.")
    return redirect("tvg_erasmus:screening-dashboard")


@login_required
@require_POST
def requirement_reminder(request, requirement_id):
    requirement = get_object_or_404(
        VerificationRequirement.all_objects.select_related(
            "case__application__opening__venture"
        ),
        public_id=requirement_id,
    )
    try:
        queue_requirement_reminder(user=request.user, requirement=requirement)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "A reminder event was queued; no email was sent.")
    return redirect("tvg_erasmus:screening-dashboard")


@login_required
@require_GET
def evidence_download(request, evidence_id):
    evidence = get_object_or_404(
        VerificationEvidence.all_objects.select_related(
            "requirement__case__application__candidate__stage_id",
            "requirement__case__application__opening__venture",
        ),
        public_id=evidence_id,
        deleted_at__isnull=True,
    )
    require_application_access(
        request.user,
        evidence.requirement.case.application,
        "tvg_erasmus.review_verification",
    )
    if evidence.scan_status != VerificationEvidence.ScanStatus.CLEAN:
        raise Http404
    try:
        response = FileResponse(
            evidence.file.open("rb"),
            as_attachment=True,
            filename=Path(evidence.original_name).name,
            content_type=evidence.content_type,
        )
    except (FileNotFoundError, OSError):
        raise Http404
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def _workflow_error(request, exc):
    messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))


@login_required
@require_GET
def arrangement_dashboard(request):
    selected_applications = (
        visible_applications(request.user)
        .filter(
            interview_invitations__booking__decision__outcome="selected",
            erasmus_case__isnull=True,
        )
        .distinct()
    )
    cases = visible_erasmus_cases(request.user).prefetch_related(
        "duration_exceptions", "acceptance_letters"
    )
    company_ids = set(
        cases.values_list("application__opening__venture__legal_employer_id", flat=True)
    ) | set(
        selected_applications.values_list(
            "opening__venture__legal_employer_id", flat=True
        )
    )
    templates = AcceptanceLetterTemplate.all_objects.filter(
        company_id__in=company_ids,
        is_active=True,
    )
    return render(
        request,
        "tvg_erasmus/arrangement_dashboard.html",
        {
            "selected_applications": selected_applications,
            "cases": cases,
            "templates": templates,
            "exception_request_form": DurationExceptionRequestForm(),
            "exception_decision_form": DurationExceptionDecisionForm(),
        },
    )


@login_required
def arrangement_edit(request, application_id):
    application = get_object_or_404(
        visible_applications(request.user), public_id=application_id
    )
    decision = get_object_or_404(
        InterviewDecision.all_objects.select_related("booking__invitation"),
        booking__invitation__application=application,
        outcome="selected",
    )
    case = ErasmusCase.all_objects.filter(application=application).first()
    form = ErasmusCaseForm(
        request.POST or None,
        instance=case,
        application=application,
    )
    if request.method == "POST" and form.is_valid():
        try:
            values = {field: form.cleaned_data[field] for field in form.Meta.fields}
            save_erasmus_case(
                user=request.user,
                application=application,
                selection_decision=decision,
                case=case,
                **values,
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _workflow_error(request, exc)
        else:
            messages.success(request, "Erasmus arrangement proposal saved.")
            return redirect("tvg_erasmus:arrangement-dashboard")
    return render(
        request,
        "tvg_erasmus/arrangement_form.html",
        {"application": application, "case": case, "form": form},
    )


@login_required
@require_POST
def duration_exception_request(request, case_id):
    case = get_object_or_404(visible_erasmus_cases(request.user), public_id=case_id)
    form = DurationExceptionRequestForm(request.POST)
    if form.is_valid():
        try:
            request_duration_exception(
                user=request.user,
                case=case,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            _workflow_error(request, exc)
        else:
            messages.success(request, "Duration exception submitted for approval.")
    else:
        messages.error(request, "A written exception reason is required.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_POST
def duration_exception_decision(request, exception_id):
    exception = get_object_or_404(
        DurationException.all_objects.select_related("case"), public_id=exception_id
    )
    form = DurationExceptionDecisionForm(request.POST)
    if form.is_valid():
        try:
            decide_duration_exception(
                user=request.user,
                exception=exception,
                decision=form.cleaned_data["decision"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _workflow_error(request, exc)
        else:
            messages.success(request, "Duration exception decision recorded.")
    else:
        messages.error(request, "The duration exception decision was invalid.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_POST
def arrangement_approve(request, case_id):
    case = get_object_or_404(visible_erasmus_cases(request.user), public_id=case_id)
    try:
        approve_erasmus_case(user=request.user, case=case)
    except ValidationError as exc:
        _workflow_error(request, exc)
    else:
        messages.success(request, "Erasmus arrangements approved by HR.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_POST
def acceptance_template_approve(request, template_id):
    template = get_object_or_404(
        AcceptanceLetterTemplate.all_objects, public_id=template_id
    )
    try:
        approve_acceptance_template(user=request.user, template=template)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _workflow_error(request, exc)
    else:
        messages.success(request, "Acceptance template approved and locked.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_POST
def acceptance_issue(request, case_id):
    case = get_object_or_404(visible_erasmus_cases(request.user), public_id=case_id)
    company = case.application.opening.venture.legal_employer
    form = AcceptanceLetterIssueForm(request.POST, company=company)
    if form.is_valid():
        try:
            issue_acceptance_letter(
                user=request.user,
                case=case,
                template=form.cleaned_data["template"],
                issue_date=form.cleaned_data["issue_date"],
            )
        except ValidationError as exc:
            _workflow_error(request, exc)
        else:
            messages.success(
                request,
                "Acceptance letter authorized and issued; it has not been sent.",
            )
    else:
        messages.error(request, "Choose an approved template and issue date.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_POST
def acceptance_send(request, letter_id):
    letter = get_object_or_404(AcceptanceLetter.all_objects, public_id=letter_id)
    try:
        send_acceptance_letter(user=request.user, letter=letter)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _workflow_error(request, exc)
    else:
        messages.success(request, "Acceptance notification queued; no email was sent.")
    return redirect("tvg_erasmus:arrangement-dashboard")


@login_required
@require_GET
def acceptance_download(request, letter_id):
    letter = get_object_or_404(
        AcceptanceLetter.all_objects.select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__venture",
        ),
        public_id=letter_id,
    )
    require_application_access(
        request.user,
        letter.case.application,
        "tvg_erasmus.issue_acceptance_letter",
    )
    try:
        response = FileResponse(
            letter.pdf_file.open("rb"),
            as_attachment=True,
            filename=f"acceptance-{letter.letter_number.replace('/', '-')}.pdf",
            content_type="application/pdf",
        )
    except (FileNotFoundError, OSError):
        raise Http404
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
