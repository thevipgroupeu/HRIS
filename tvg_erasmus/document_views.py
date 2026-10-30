from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from tvg_erasmus.document_services import (
    approve_document,
    create_learning_agreement_amendment,
    generate_from_approved_template,
    initialize_document_lifecycle,
    record_signature,
    request_signing_service,
    return_signed_document,
    review_document,
    staff_document_version,
    staff_upload_document,
    update_accommodation_follow_up,
    upload_manual_signed_document,
    visible_case_documents,
)
from tvg_erasmus.forms import (
    ErasmusAccommodationFollowUpForm,
    ErasmusDocumentReviewForm,
    ErasmusDocumentUploadForm,
    ErasmusManualSignatureForm,
    ErasmusRecordSignatureForm,
)
from tvg_erasmus.models import ErasmusCase, ErasmusDocumentVersion
from tvg_erasmus.services import visible_erasmus_cases


def _error(request, exc):
    messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))


@login_required
@require_GET
def dashboard(request):
    cases = visible_erasmus_cases(request.user).prefetch_related(
        "documents__versions__signatures", "documents__events"
    )
    return render(
        request,
        "tvg_erasmus/document_dashboard.html",
        {"cases": cases},
    )


@login_required
@require_POST
def initialize(request, case_id):
    case = get_object_or_404(visible_erasmus_cases(request.user), public_id=case_id)
    try:
        initialize_document_lifecycle(user=request.user, case=case)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(request, "Learning Agreement lifecycle opened.")
    return redirect("tvg_erasmus:document-dashboard")


def _document(request, document_id):
    return get_object_or_404(
        visible_case_documents(request.user).select_related(
            "case__application__opening__venture", "template"
        ),
        public_id=document_id,
    )


@login_required
@require_POST
def upload(request, document_id):
    document = _document(request, document_id)
    form = ErasmusDocumentUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            staff_upload_document(
                user=request.user,
                document=document,
                upload=form.cleaned_data["document"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _error(request, exc)
        else:
            messages.success(request, "A new immutable document version was uploaded.")
    else:
        messages.error(request, "Upload a safe PDF or DOCX file no larger than 10 MB.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def generate(request, document_id):
    document = _document(request, document_id)
    try:
        generate_from_approved_template(user=request.user, document=document)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(
            request,
            "Approved template copied as a draft version; it is not a signature or final legal document.",
        )
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def review(request, document_id):
    document = _document(request, document_id)
    form = ErasmusDocumentReviewForm(request.POST)
    if form.is_valid():
        try:
            review_document(
                user=request.user,
                document=document,
                revision_reason=(
                    form.cleaned_data["revision_reason"]
                    if form.cleaned_data["action"] == "revision"
                    else ""
                ),
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _error(request, exc)
        else:
            messages.success(request, "Document review recorded.")
    else:
        messages.error(request, "A revision request requires a reason.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def approve(request, document_id):
    document = _document(request, document_id)
    try:
        approve_document(user=request.user, document=document)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(request, "Document approved by an authorized human.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def manual_signed_upload(request, document_id):
    document = _document(request, document_id)
    form = ErasmusManualSignatureForm(request.POST, request.FILES, document=document)
    if form.is_valid():
        try:
            upload_manual_signed_document(
                user=request.user,
                document=document,
                upload=form.cleaned_data["document"],
                signed_roles=form.cleaned_data["signer_roles"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _error(request, exc)
        else:
            messages.success(request, "Manual signed copy and attestations recorded.")
    else:
        messages.error(request, "Choose signer roles and upload a valid signed copy.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def signature(request, document_id):
    document = _document(request, document_id)
    form = ErasmusRecordSignatureForm(request.POST, document=document)
    if form.is_valid():
        try:
            record_signature(
                user=request.user,
                document=document,
                signer_role=form.cleaned_data["signer_role"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _error(request, exc)
        else:
            messages.success(
                request, "Signature timestamp recorded against the latest version."
            )
    else:
        messages.error(request, "Choose a required signer role.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def signing_service(request, document_id):
    document = _document(request, document_id)
    try:
        request_signing_service(user=request.user, document=document)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(
            request, "Signing request recorded with the configured service."
        )
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def return_document(request, document_id):
    document = _document(request, document_id)
    try:
        return_signed_document(user=request.user, document=document)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(request, "Signed document marked as returned.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def amendment(request, document_id):
    document = _document(request, document_id)
    try:
        create_learning_agreement_amendment(user=request.user, document=document)
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        _error(request, exc)
    else:
        messages.success(request, "Learning Agreement amendment lifecycle opened.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_POST
def accommodation_follow_up(request, case_id):
    case = get_object_or_404(visible_erasmus_cases(request.user), public_id=case_id)
    form = ErasmusAccommodationFollowUpForm(request.POST)
    if form.is_valid():
        try:
            update_accommodation_follow_up(
                user=request.user,
                case=case,
                status=form.cleaned_data["status"],
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            _error(request, exc)
        else:
            messages.success(request, "Accommodation follow-up status updated.")
    else:
        messages.error(request, "Choose a valid accommodation follow-up status.")
    return redirect("tvg_erasmus:document-dashboard")


@login_required
@require_GET
def download(request, version_id):
    version = get_object_or_404(
        ErasmusDocumentVersion.all_objects.select_related(
            "document__case__application__candidate__stage_id",
            "document__case__application__opening__recruitment",
            "document__case__application__opening__venture",
        ),
        public_id=version_id,
    )
    try:
        staff_document_version(user=request.user, version=version)
        response = FileResponse(
            version.file.open("rb"),
            as_attachment=True,
            filename=Path(version.original_filename).name,
            content_type=version.content_type,
        )
    except PermissionDenied:
        raise
    except (FileNotFoundError, OSError):
        raise Http404
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
