from __future__ import annotations

from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tvg_careers.accounts.application_client import (
    ApplicationConflict,
    ApplicationServiceError,
    get_application_client,
)
from tvg_careers.accounts.forms import (
    CandidateAccommodationForm,
    CandidateApplicationForm,
    CandidateArrangementDatesForm,
    CandidateDocumentUploadForm,
    CandidateErasmusDocumentUploadForm,
    CandidateInterviewActionForm,
    CandidateVerificationUploadForm,
)
from tvg_careers.accounts.models import (
    CandidateApplication,
    CandidateApplicationStatusEvent,
    CandidateProfile,
    CandidateProfileDocument,
    CandidateSecurityEvent,
)
from tvg_careers.accounts.portal_services import (
    submit_application,
    sync_candidate_dashboard,
)
from tvg_careers.accounts.services import log_security_event
from tvg_careers.accounts.throttling import client_ip, consume_rate
from tvg_careers.attribution import attribution_for_request
from tvg_careers.public_client import (
    CareerHubAPIError,
    CareerHubJobNotFound,
    get_public_job_client,
)
from tvg_careers.public_views import _base_context


def _context(request, *, title, description):
    context = _base_context(request, title=title, description=description)
    context["robots"] = "noindex, nofollow"
    return context


def _candidate_ready(account):
    return account.applicant_functions_enabled


def _download_response(field, filename, content_type):
    if not field:
        raise Http404
    try:
        response = FileResponse(
            field.open("rb"),
            as_attachment=True,
            filename=Path(filename).name,
            content_type=content_type,
        )
    except (FileNotFoundError, OSError):
        raise Http404
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def dashboard(request):
    applications = list(
        CandidateApplication.objects.filter(account=request.user).prefetch_related(
            "status_events"
        )
    )
    remote_by_id = {}
    status_unavailable = False
    if _candidate_ready(request.user) and any(
        item.state != CandidateApplication.State.DRAFT for item in applications
    ):
        try:
            remote_by_id = sync_candidate_dashboard(
                request.user,
                candidate_timezone=request.session.get(
                    "candidate_timezone", "Europe/Warsaw"
                ),
            )
            applications = list(
                CandidateApplication.objects.filter(
                    account=request.user
                ).prefetch_related("status_events")
            )
        except ApplicationServiceError:
            status_unavailable = True

    interviews = []
    offers = []
    onboarding = []
    notifications = []
    verifications = []
    arrangements = []
    documentation = []
    for application in applications:
        remote = remote_by_id.get(str(application.pk), {})
        for interview in remote.get("interviews", []):
            interviews.append({"application": application, **interview})
            for item in interview.get("notifications", []):
                notifications.append(
                    {
                        "application": application,
                        "message": item.get("message", "Interview update."),
                    }
                )
        if remote.get("offer"):
            offers.append({"application": application, **remote["offer"]})
        if remote.get("onboarding", {}).get("status") != "Not started":
            onboarding.append({"application": application, **remote["onboarding"]})
        verification = remote.get("verification", {})
        if verification and (
            verification.get("status") != "not_requested"
            or verification.get("next_action")
            != "No verification action is currently required."
        ):
            verifications.append(
                {"application": application, "verification": verification}
            )
        for item in verification.get("notifications", []):
            notifications.append(
                {
                    "application": application,
                    "message": item.get("message", "Verification update."),
                }
            )
        arrangement = remote.get("arrangement")
        if arrangement:
            arrangements.append(
                {"application": application, "arrangement": arrangement}
            )
            for item in arrangement.get("notifications", []):
                notifications.append(
                    {
                        "application": application,
                        "message": item.get("message", "Acceptance update."),
                    }
                )
        document_lifecycle = remote.get("documentation")
        if document_lifecycle:
            documentation.append(
                {"application": application, "documentation": document_lifecycle}
            )
        if application.state == CandidateApplication.State.DRAFT:
            notifications.append(
                {
                    "application": application,
                    "message": "Your application is still a draft.",
                }
            )
        else:
            notifications.append(
                {
                    "application": application,
                    "message": f"Current status: {application.friendly_status_label}.",
                }
            )

    context = _context(
        request,
        title="Candidate dashboard",
        description="Your applications and candidate activity.",
    )
    context.update(
        {
            "applications": applications,
            "interviews": interviews,
            "offers": offers,
            "onboarding": onboarding,
            "verifications": verifications,
            "arrangements": arrangements,
            "documentation": documentation,
            "notifications": notifications,
            "status_unavailable": status_unavailable,
        }
    )
    return render(request, "careerhub/accounts/dashboard.html", context)


def _interview_rate_allowed(request):
    account_result = consume_rate("interview_account", str(request.user.pk))
    ip_result = consume_rate("interview_ip", client_ip(request))
    return account_result.allowed and ip_result.allowed


def _arrangement_rate_allowed(request):
    account_result = consume_rate("arrangement_account", str(request.user.pk))
    ip_result = consume_rate("arrangement_ip", client_ip(request))
    return account_result.allowed and ip_result.allowed


def _owned_submitted_application(request, application_id):
    if not _candidate_ready(request.user):
        raise Http404
    return get_object_or_404(
        CandidateApplication.objects.filter(
            account=request.user,
            state=CandidateApplication.State.SUBMITTED,
        ),
        pk=application_id,
    )


def _owned_remote_arrangement(request, application, case_id):
    try:
        remote_by_id = sync_candidate_dashboard(request.user)
    except ApplicationServiceError:
        return None
    arrangement = remote_by_id.get(str(application.pk), {}).get("arrangement")
    if not arrangement or arrangement.get("id") != str(case_id):
        raise Http404
    return arrangement


def _owned_remote_documentation(request, application, case_id):
    try:
        remote_by_id = sync_candidate_dashboard(request.user)
    except ApplicationServiceError:
        return None
    documentation = remote_by_id.get(str(application.pk), {}).get("documentation")
    if not documentation or documentation.get("case_id") != str(case_id):
        raise Http404
    return documentation


@login_required
@require_POST
def erasmus_document_upload(request, application_id, case_id, document_id):
    application = _owned_submitted_application(request, application_id)
    if not _arrangement_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="Document actions are temporarily rate limited.",
            ),
            status=429,
        )
    documentation = _owned_remote_documentation(request, application, case_id)
    if documentation is None:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Document service unavailable",
                description="Your document was not changed.",
            ),
            status=503,
        )
    document = next(
        (
            item
            for item in documentation.get("documents", [])
            if item.get("id") == str(document_id) and item.get("can_upload")
        ),
        None,
    )
    if not document:
        raise Http404
    form = CandidateErasmusDocumentUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Document could not be uploaded",
                description="Upload a safe PDF or DOCX document no larger than 10 MB.",
            ),
            status=400,
        )
    try:
        get_application_client().upload_erasmus_document(
            case_id=case_id,
            document_id=document_id,
            person_public_id=request.user.person_public_id,
            document=form.cleaned_data["document"],
        )
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Document could not be submitted",
                description="The document state changed; reload and try again.",
            ),
            status=409,
        )
    return redirect(
        f"{reverse('careerhub:candidate-dashboard')}?erasmus_document=submitted"
    )


@login_required
@require_GET
def erasmus_document_download(
    request, application_id, case_id, document_id, version_id
):
    application = _owned_submitted_application(request, application_id)
    if not _arrangement_rate_allowed(request):
        raise Http404
    documentation = _owned_remote_documentation(request, application, case_id)
    if documentation is None:
        raise Http404
    document = next(
        (
            item
            for item in documentation.get("documents", [])
            if item.get("id") == str(document_id)
        ),
        None,
    )
    version = next(
        (
            item
            for item in (document or {}).get("versions", [])
            if item.get("id") == str(version_id)
        ),
        None,
    )
    if not version:
        raise Http404
    try:
        content, content_type = get_application_client().download_erasmus_document(
            case_id=case_id,
            document_id=document_id,
            version_id=version_id,
            person_public_id=request.user.person_public_id,
        )
    except ApplicationServiceError:
        raise Http404
    response = HttpResponse(content.content, content_type=content_type)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{Path(version["filename"]).name}"'
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@login_required
@require_POST
def accommodation_response(request, application_id, case_id):
    application = _owned_submitted_application(request, application_id)
    if not _arrangement_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="This action is temporarily rate limited.",
            ),
            status=429,
        )
    documentation = _owned_remote_documentation(request, application, case_id)
    if documentation is None:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Document service unavailable",
                description="Your response was not changed.",
            ),
            status=503,
        )
    if not documentation.get("accommodation_question"):
        raise Http404
    form = CandidateAccommodationForm(request.POST)
    if not form.is_valid():
        raise Http404
    try:
        get_application_client().record_accommodation_response(
            case_id=case_id,
            person_public_id=request.user.person_public_id,
            needs_assistance=form.cleaned_data["needs_assistance"],
        )
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Response could not be recorded",
                description="The Learning Agreement state changed; reload and retry.",
            ),
            status=409,
        )
    return redirect(
        f"{reverse('careerhub:candidate-dashboard')}?accommodation=recorded"
    )


@login_required
@require_POST
def arrangement_dates_confirm(request, application_id, case_id):
    application = _owned_submitted_application(request, application_id)
    arrangement = _owned_remote_arrangement(request, application, case_id)
    if arrangement is None:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Arrangement service unavailable",
                description="Your dates were not changed.",
            ),
            status=503,
        )
    if not arrangement.get("can_confirm_dates"):
        raise Http404
    if not _arrangement_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="Date confirmation is temporarily rate limited.",
            ),
            status=429,
        )
    form = CandidateArrangementDatesForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Dates were not valid",
                description="Provide a start date that is not after the end date.",
            ),
            status=400,
        )
    try:
        get_application_client().confirm_arrangement_dates(
            case_id=case_id,
            person_public_id=request.user.person_public_id,
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
        )
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Dates could not be confirmed",
                description=(
                    "The dates conflict with policy or another confirmed arrangement."
                ),
            ),
            status=409,
        )
    log_security_event(
        CandidateSecurityEvent.EventType.ARRANGEMENT_DATES_CONFIRMED,
        request=request,
        account=request.user,
        metadata={"application_id": str(application.pk), "case_id": str(case_id)},
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?arrangement=confirmed")


@login_required
@require_POST
def acceptance_receipt(request, application_id, case_id, letter_id):
    application = _owned_submitted_application(request, application_id)
    arrangement = _owned_remote_arrangement(request, application, case_id)
    if arrangement is None:
        raise Http404
    letter = next(
        (
            item
            for item in arrangement.get("letters", [])
            if item.get("id") == str(letter_id)
        ),
        None,
    )
    if not letter or not letter.get("can_confirm_receipt"):
        raise Http404
    if not _arrangement_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="Receipt confirmation is temporarily rate limited.",
            ),
            status=429,
        )
    try:
        get_application_client().confirm_acceptance_receipt(
            case_id=case_id,
            letter_id=letter_id,
            person_public_id=request.user.person_public_id,
        )
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Receipt could not be confirmed",
                description="The acceptance document state changed; reload and retry.",
            ),
            status=409,
        )
    log_security_event(
        CandidateSecurityEvent.EventType.ACCEPTANCE_RECEIVED,
        request=request,
        account=request.user,
        metadata={"application_id": str(application.pk), "letter_id": str(letter_id)},
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?acceptance=received")


@login_required
@require_GET
def acceptance_download(request, application_id, case_id, letter_id):
    application = _owned_submitted_application(request, application_id)
    arrangement = _owned_remote_arrangement(request, application, case_id)
    if arrangement is None:
        raise Http404
    letter = next(
        (
            item
            for item in arrangement.get("letters", [])
            if item.get("id") == str(letter_id) and item.get("downloadable")
        ),
        None,
    )
    if not letter:
        raise Http404
    try:
        document = get_application_client().download_acceptance(
            case_id=case_id,
            letter_id=letter_id,
            person_public_id=request.user.person_public_id,
        )
    except ApplicationServiceError:
        raise Http404
    log_security_event(
        CandidateSecurityEvent.EventType.ACCEPTANCE_DOWNLOADED,
        request=request,
        account=request.user,
        metadata={"application_id": str(application.pk), "letter_id": str(letter_id)},
    )
    filename = (
        f"acceptance-{str(letter.get('letter_number', 'letter')).replace('/', '-')}.pdf"
    )
    response = HttpResponse(document.content, content_type="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@login_required
@require_POST
def interview_book(request, application_id, invitation_id):
    application = _owned_submitted_application(request, application_id)
    form = CandidateInterviewActionForm(request.POST)
    if not _interview_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="Interview booking is temporarily rate limited.",
            ),
            status=429,
        )
    if not form.is_valid():
        raise Http404
    try:
        get_application_client().book_interview(
            invitation_id=invitation_id,
            slot_id=form.cleaned_data["slot_id"],
            person_public_id=request.user.person_public_id,
            candidate_timezone=form.cleaned_data["candidate_timezone"],
        )
    except (ApplicationConflict, ApplicationServiceError):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Interview slot unavailable",
                description="The slot could not be booked. Refresh your dashboard and choose another available time.",
            ),
            status=409,
        )
    request.session["candidate_timezone"] = form.cleaned_data["candidate_timezone"]
    log_security_event(
        CandidateSecurityEvent.EventType.INTERVIEW_BOOKED,
        request=request,
        account=request.user,
        metadata={
            "application_id": str(application.pk),
            "invitation_id": str(invitation_id),
        },
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?interview=booked")


@login_required
@require_POST
def interview_reschedule(request, application_id, invitation_id):
    application = _owned_submitted_application(request, application_id)
    form = CandidateInterviewActionForm(request.POST)
    if not _interview_rate_allowed(request):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Please wait before trying again",
                description="Interview rescheduling is temporarily rate limited.",
            ),
            status=429,
        )
    if not form.is_valid():
        raise Http404
    try:
        get_application_client().reschedule_interview(
            invitation_id=invitation_id,
            slot_id=form.cleaned_data["slot_id"],
            person_public_id=request.user.person_public_id,
            candidate_timezone=form.cleaned_data["candidate_timezone"],
        )
    except (ApplicationConflict, ApplicationServiceError):
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Interview could not be rescheduled",
                description="The reschedule policy or slot availability prevented this change.",
            ),
            status=409,
        )
    request.session["candidate_timezone"] = form.cleaned_data["candidate_timezone"]
    log_security_event(
        CandidateSecurityEvent.EventType.INTERVIEW_RESCHEDULED,
        request=request,
        account=request.user,
        metadata={
            "application_id": str(application.pk),
            "invitation_id": str(invitation_id),
        },
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?interview=rescheduled")


@login_required
@require_POST
def verification_upload(request, application_id, requirement_id):
    application = get_object_or_404(
        CandidateApplication.objects.filter(
            account=request.user,
            state=CandidateApplication.State.SUBMITTED,
        ),
        pk=application_id,
    )
    if not _candidate_ready(request.user):
        raise Http404
    try:
        remote_by_id = sync_candidate_dashboard(request.user)
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Verification service unavailable",
                description="Your document was not submitted.",
            ),
            status=503,
        )
    verification = remote_by_id.get(str(application.pk), {}).get("verification", {})
    requirement = next(
        (
            item
            for item in verification.get("requirements", [])
            if item.get("id") == str(requirement_id)
        ),
        None,
    )
    if not requirement or requirement.get("status") not in {
        "requested",
        "revision_needed",
    }:
        raise Http404
    form = CandidateVerificationUploadForm(
        request.POST,
        request.FILES,
        requirement=requirement,
    )
    if not form.is_valid():
        context = _context(
            request,
            title="Verification document not accepted",
            description="Correct the document and try again.",
        )
        context.update({"form": form, "requirement": requirement})
        return render(
            request,
            "careerhub/accounts/verification_upload_error.html",
            context,
            status=400,
        )
    upload = form.cleaned_data["document"]
    document = type(
        "VerificationDocument",
        (),
        {
            "name": Path(upload.name).name,
            "content": form.content,
            "content_type": form.content_type,
        },
    )()
    try:
        get_application_client().upload_verification(
            application_id=application.pk,
            person_public_id=request.user.person_public_id,
            requirement_id=requirement_id,
            document=document,
        )
    except ApplicationServiceError:
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            _context(
                request,
                title="Verification service unavailable",
                description="Your document was not submitted.",
            ),
            status=503,
        )
    log_security_event(
        CandidateSecurityEvent.EventType.VERIFICATION_UPLOADED,
        request=request,
        account=request.user,
        metadata={
            "application_id": str(application.pk),
            "requirement_id": str(requirement_id),
        },
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?verification=uploaded")


@login_required
@require_http_methods(["GET", "POST"])
def documents(request):
    context = _context(
        request,
        title="My documents",
        description="Manage reusable private application documents.",
    )
    form = CandidateDocumentUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["document"]
        kind = form.cleaned_data["kind"]
        with transaction.atomic():
            CandidateProfileDocument.objects.select_for_update(of=("self",)).filter(
                account=request.user,
                kind=kind,
                is_current=True,
            ).update(is_current=False)
            document = CandidateProfileDocument(
                account=request.user,
                kind=kind,
                original_name=Path(upload.name).name[:255],
                content_type=form.content_type,
                size_bytes=len(form.content),
                sha256=form.sha256,
            )
            document.file.save(
                document.original_name,
                ContentFile(form.content),
                save=False,
            )
            document.save()
        log_security_event(
            CandidateSecurityEvent.EventType.DOCUMENT_UPLOADED,
            request=request,
            account=request.user,
            metadata={"kind": kind},
        )
        return redirect(f"{reverse('careerhub:candidate-documents')}?saved=1")
    context.update(
        {
            "form": form,
            "documents": CandidateProfileDocument.objects.filter(
                account=request.user, is_current=True
            ),
            "saved": request.GET.get("saved") == "1",
        }
    )
    status = 400 if request.method == "POST" and form.errors else 200
    return render(request, "careerhub/accounts/documents.html", context, status=status)


@login_required
@require_GET
def document_download(request, document_id):
    document = CandidateProfileDocument.objects.filter(
        pk=document_id,
        account=request.user,
        is_current=True,
    ).first()
    if not document:
        raise Http404
    return _download_response(
        document.file,
        document.original_name,
        document.content_type,
    )


@login_required
@require_GET
def application_document_download(request, application_id, kind):
    application = CandidateApplication.objects.filter(
        pk=application_id,
        account=request.user,
    ).first()
    if not application or application.state == CandidateApplication.State.DRAFT:
        raise Http404
    if kind == "cv":
        return _download_response(
            application.cv_snapshot,
            application.cv_original_name,
            application.cv_content_type,
        )
    if kind == "cover-letter":
        return _download_response(
            application.cover_letter_snapshot,
            application.cover_letter_original_name,
            application.cover_letter_content_type,
        )
    raise Http404


@login_required
@require_http_methods(["GET", "POST"])
def application_create(request, venture_slug, opening_slug):
    if not _candidate_ready(request.user):
        context = _context(
            request,
            title="Application unavailable",
            description="Your identity must be linked before you can apply.",
        )
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            context,
            status=403,
        )
    try:
        job = get_public_job_client().get_job(venture_slug, opening_slug)
    except CareerHubJobNotFound:
        raise Http404
    except CareerHubAPIError:
        context = _context(
            request,
            title="Application service unavailable",
            description="The role service is temporarily unavailable.",
        )
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            context,
            status=503,
        )
    if not job.get("accepting_applications"):
        return redirect(f"{job['url_path']}?application=closed")

    attribution = attribution_for_request(request)
    try:
        application, created = CandidateApplication.objects.get_or_create(
            account=request.user,
            opening_public_id=job["public_id"],
            defaults={
                "opening_slug": job["slug"],
                "opening_title": job["title"],
                "venture_slug": job["venture"]["slug"],
                "venture_name": job["venture"]["name"],
                "source": attribution["source"],
                "utm_parameters": attribution["utm_parameters"],
            },
        )
    except IntegrityError:
        application = CandidateApplication.objects.get(
            account=request.user,
            opening_public_id=job["public_id"],
        )
        created = False
    if not created and application.state != CandidateApplication.State.DRAFT:
        return redirect("careerhub:candidate-dashboard")

    profile, _ = CandidateProfile.objects.get_or_create(account=request.user)
    current_documents = {
        item.kind: item
        for item in CandidateProfileDocument.objects.filter(
            account=request.user, is_current=True
        )
    }
    form = CandidateApplicationForm(request.POST or None, job=job)
    submit_error = ""
    submit_status = 200
    if request.method == "POST" and form.is_valid():
        if CandidateProfileDocument.Kind.CV not in current_documents:
            form.add_error(None, "Upload a current CV before submitting.")
        else:
            try:
                application = submit_application(
                    application=application,
                    account=request.user,
                    profile=profile,
                    questionnaire=form.questionnaire_snapshot(),
                    questionnaire_version=job["questionnaire_version"],
                )
            except ApplicationConflict:
                submit_error = "An application for this opportunity already exists."
                submit_status = 409
            except (ApplicationServiceError, OSError):
                submit_error = (
                    "We could not submit your application just now. Your draft is safe."
                )
                submit_status = 503
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                log_security_event(
                    CandidateSecurityEvent.EventType.APPLICATION_SUBMITTED,
                    request=request,
                    account=request.user,
                    metadata={"application_id": str(application.pk)},
                )
                return redirect(
                    f"{reverse('careerhub:candidate-dashboard')}?submitted=1"
                )

    context = _context(
        request,
        title=f"Apply for {job['title']}",
        description=f"Submit your application to {job['venture']['name']}.",
    )
    context.update(
        {
            "application": application,
            "job": job,
            "profile": profile,
            "documents": current_documents,
            "form": form,
            "submit_error": submit_error,
        }
    )
    status = submit_status
    if request.method == "POST" and form.errors:
        status = 400
    return render(
        request, "careerhub/accounts/application_form.html", context, status=status
    )


@login_required
@require_POST
def application_withdraw(request, application_id):
    application = CandidateApplication.objects.filter(
        pk=application_id,
        account=request.user,
        state=CandidateApplication.State.SUBMITTED,
    ).first()
    if not application:
        raise Http404
    try:
        result = get_application_client().withdraw(
            application_id=application.pk,
            person_public_id=request.user.person_public_id,
        )
    except ApplicationServiceError:
        context = _context(
            request,
            title="Withdrawal unavailable",
            description="Your application has not been changed.",
        )
        return render(
            request,
            "careerhub/accounts/application_unavailable.html",
            context,
            status=503,
        )
    status = result.get("status") or {"code": "withdrawn", "label": "Withdrawn"}
    now = timezone.now()
    with transaction.atomic():
        application = CandidateApplication.objects.select_for_update(of=("self",)).get(
            pk=application.pk
        )
        application.state = CandidateApplication.State.WITHDRAWN
        application.withdrawn_at = now
        application.friendly_status_code = str(status.get("code", "withdrawn"))[:32]
        application.friendly_status_label = str(status.get("label", "Withdrawn"))[:80]
        application.status_updated_at = now
        application.save(
            update_fields=(
                "state",
                "withdrawn_at",
                "friendly_status_code",
                "friendly_status_label",
                "status_updated_at",
                "updated_at",
            )
        )
        CandidateApplicationStatusEvent.objects.create(
            application=application,
            code=application.friendly_status_code,
            label=application.friendly_status_label,
            occurred_at=now,
        )
    log_security_event(
        CandidateSecurityEvent.EventType.APPLICATION_WITHDRAWN,
        request=request,
        account=request.user,
        metadata={"application_id": str(application.pk)},
    )
    return redirect(f"{reverse('careerhub:candidate-dashboard')}?withdrawn=1")
