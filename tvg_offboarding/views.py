from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods, require_POST

from asset.models import AssetAssignment
from tvg_onboarding.models import InternOnboarding
from tvg_peoplehub.views import _base_context, _render

from .forms import (
    AssetReturnForm,
    DocumentPrepareForm,
    ExceptionForm,
    FinalFeedbackForm,
    FinalPresentationScheduleForm,
    FinalReportLinkForm,
    ManualRevocationForm,
    SignatureForm,
)
from .models import (
    AccessRevocationTask,
    CompletionCase,
    CompletionDocument,
    CompletionRequirement,
    ReferenceLetterRequest,
)
from .services import (
    approve_access_exception,
    attempt_access_revocation,
    can_view_case,
    complete_internship,
    complete_manual_access_revocation,
    confirm_administrative_closure,
    generate_completion_document,
    handle_reference_request,
    issue_completion_certificate,
    link_final_monthly_presentation,
    link_final_weekly_report,
    management_cases,
    record_document_signature,
    record_final_supervisor_feedback,
    request_reference_letter,
    return_asset,
    schedule_final_presentation,
    start_completion,
)


def _form_error(form, error):
    if hasattr(error, "message_dict"):
        for field, messages in error.message_dict.items():
            target = field if field in form.fields else None
            for message in messages:
                form.add_error(target, message)
    else:
        for message in getattr(error, "messages", [str(error)]):
            form.add_error(None, message)


def _case(request, public_id):
    viewer, context = _base_context(request)
    case = get_object_or_404(
        CompletionCase.all_objects.select_related(
            "employee",
            "onboarding__supervisor",
            "onboarding__erasmus_case",
            "onboarding__venture__legal_employer",
        ),
        public_id=public_id,
    )
    if not can_view_case(request.user, viewer, case):
        raise PermissionDenied("You cannot view this completion case.")
    return viewer, context, case


@login_required
def completion_home(request):
    viewer, context = _base_context(request)
    own = CompletionCase.all_objects.filter(employee=viewer).select_related(
        "employee", "onboarding__supervisor"
    )
    managed = CompletionCase.all_objects.none()
    eligible = InternOnboarding.all_objects.none()
    if (
        request.user.is_superuser
        or request.user.has_perm("tvg_offboarding.view_completion_management")
        or request.user.has_perm("tvg_offboarding.manage_intern_completion")
    ):
        managed = management_cases(user=request.user)
    if request.user.is_superuser or request.user.has_perm(
        "tvg_offboarding.start_intern_completion"
    ):
        eligible = (
            InternOnboarding.all_objects.filter(
                venture__legal_employer=viewer.get_company(),
                status=InternOnboarding.Status.ONBOARDED,
                employee__isnull=False,
                completion_case__isnull=True,
            )
            .select_related("employee", "supervisor")
            .order_by("end_date")
        )
    context.update(
        {
            "own_cases": own,
            "managed_cases": managed,
            "eligible_onboardings": eligible,
        }
    )
    return _render(request, "tvg_offboarding/home.html", context)


@login_required
def completion_detail(request, public_id):
    viewer, context, case = _case(request, public_id)
    context.update(
        {
            "case": case,
            "requirements": case.requirements.select_related("completed_by"),
            "assets": AssetAssignment._base_manager.filter(
                assigned_to_employee_id=case.employee
            ).select_related("asset_id", "asset_item_id"),
            "access_tasks": case.access_revocations.all(),
            "documents": case.documents.prefetch_related("signatures"),
            "university_documents": case.onboarding.erasmus_case.documents.filter(
                lifecycle_phase="after_internship"
            ),
            "events": case.events.select_related("actor").order_by("-occurred_at")[:50],
            "is_intern": viewer.pk == case.employee_id,
            "can_manage": request.user.is_superuser
            or request.user.has_perm("tvg_offboarding.manage_intern_completion"),
            "can_manage_assets": request.user.is_superuser
            or request.user.has_perm("tvg_offboarding.manage_completion_assets"),
            "can_manage_access": request.user.is_superuser
            or request.user.has_perm("tvg_offboarding.manage_access_revocation"),
            "can_complete": request.user.is_superuser
            or request.user.has_perm("tvg_offboarding.complete_intern_completion"),
            "can_exception": request.user.is_superuser
            or request.user.has_perm("tvg_offboarding.approve_completion_exception"),
        }
    )
    return _render(request, "tvg_offboarding/detail.html", context)


@login_required
@require_POST
def completion_start(request, onboarding_id):
    onboarding = get_object_or_404(
        InternOnboarding.all_objects, public_id=onboarding_id
    )
    try:
        case = start_completion(user=request.user, onboarding=onboarding)
    except (PermissionDenied, ValidationError):
        raise
    return redirect("completion:detail", public_id=case.public_id)


def _form_action(request, public_id, form_class, title, callback, *, multipart=False):
    _viewer, context, case = _case(request, public_id)
    form = form_class(
        request.POST or None,
        request.FILES or None,
        case=case,
    )
    if request.method == "POST" and form.is_valid():
        try:
            callback(case, form.cleaned_data)
            return redirect("completion:detail", public_id=case.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update({"form": form, "title": title, "multipart": multipart})
    return _render(request, "tvg_peoplehub/form.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def presentation_schedule(request, public_id):
    return _form_action(
        request,
        public_id,
        FinalPresentationScheduleForm,
        "Schedule final presentation",
        lambda case, data: schedule_final_presentation(
            user=request.user, case=case, **data
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def report_link(request, public_id):
    def link(case, data):
        link_final_weekly_report(
            user=request.user, case=case, report=data["weekly_report"]
        )
        link_final_monthly_presentation(
            user=request.user,
            case=case,
            presentation=data["monthly_presentation"],
        )

    return _form_action(
        request,
        public_id,
        FinalReportLinkForm,
        "Link approved final reporting",
        link,
    )


@login_required
@require_http_methods(["GET", "POST"])
def final_feedback(request, public_id):
    return _form_action(
        request,
        public_id,
        FinalFeedbackForm,
        "Record final supervisor feedback",
        lambda case, data: record_final_supervisor_feedback(
            user=request.user, case=case, **data
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def asset_return(request, public_id):
    return _form_action(
        request,
        public_id,
        AssetReturnForm,
        "Record asset return",
        lambda case, data: return_asset(user=request.user, case=case, **data),
    )


@login_required
@require_POST
def access_attempt(request, public_id, task_id):
    _viewer, _context, case = _case(request, public_id)
    task = get_object_or_404(
        AccessRevocationTask.all_objects, public_id=task_id, case=case
    )
    attempt_access_revocation(user=request.user, task=task)
    return redirect("completion:detail", public_id=case.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def access_manual(request, public_id, task_id):
    _viewer, context, case = _case(request, public_id)
    task = get_object_or_404(
        AccessRevocationTask.all_objects, public_id=task_id, case=case
    )
    form = ManualRevocationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            complete_manual_access_revocation(
                user=request.user, task=task, **form.cleaned_data
            )
            return redirect("completion:detail", public_id=case.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update(
        {
            "form": form,
            "title": f"Complete {task.system_label} revocation",
            "multipart": True,
        }
    )
    return _render(request, "tvg_peoplehub/form.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def access_exception(request, public_id, task_id):
    _viewer, context, case = _case(request, public_id)
    task = get_object_or_404(
        AccessRevocationTask.all_objects, public_id=task_id, case=case
    )
    form = ExceptionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            approve_access_exception(user=request.user, task=task, **form.cleaned_data)
            return redirect("completion:detail", public_id=case.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update({"form": form, "title": f"Authorize {task.system_label} exception"})
    return _render(request, "tvg_peoplehub/form.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def document_prepare(request, public_id):
    return _form_action(
        request,
        public_id,
        DocumentPrepareForm,
        "Prepare completion document",
        lambda case, data: generate_completion_document(
            user=request.user, case=case, **data
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def document_sign(request, public_id, document_id):
    _viewer, context, case = _case(request, public_id)
    document = get_object_or_404(
        CompletionDocument.all_objects, public_id=document_id, case=case
    )
    form = SignatureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            record_document_signature(
                user=request.user, document=document, **form.cleaned_data
            )
            return redirect("completion:detail", public_id=case.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update({"form": form, "title": "Record human signature"})
    return _render(request, "tvg_peoplehub/form.html", context)


@login_required
@require_POST
def document_issue(request, public_id, document_id):
    _viewer, _context, case = _case(request, public_id)
    document = get_object_or_404(
        CompletionDocument.all_objects, public_id=document_id, case=case
    )
    issue_completion_certificate(user=request.user, document=document)
    return redirect("completion:detail", public_id=case.public_id)


@login_required
def document_download(request, public_id, document_id):
    _viewer, _context, case = _case(request, public_id)
    document = get_object_or_404(
        CompletionDocument.all_objects, public_id=document_id, case=case
    )
    try:
        handle = document.pdf_file.open("rb")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise Http404("Document unavailable.") from exc
    response = FileResponse(
        handle,
        as_attachment=True,
        filename=f"{document.kind}-v{document.version_number}.pdf",
        content_type="application/pdf",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def reference_request(request, public_id):
    _viewer, _context, case = _case(request, public_id)
    request_reference_letter(user=request.user, case=case)
    return redirect("completion:detail", public_id=case.public_id)


@login_required
@require_POST
def reference_handle(request, public_id):
    _viewer, _context, case = _case(request, public_id)
    reference = get_object_or_404(ReferenceLetterRequest.all_objects, case=case)
    try:
        handle_reference_request(
            user=request.user, request=reference, status=request.POST.get("status", "")
        )
    except ValidationError as error:
        return HttpResponseBadRequest(
            " ".join(error.messages), content_type="text/plain"
        )
    return redirect("completion:detail", public_id=case.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def requirement_exception(request, public_id, requirement_id):
    _viewer, context, case = _case(request, public_id)
    requirement = get_object_or_404(
        CompletionRequirement.all_objects, pk=requirement_id, case=case
    )
    form = ExceptionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from .services import approve_requirement_exception

        try:
            approve_requirement_exception(
                user=request.user, requirement=requirement, **form.cleaned_data
            )
            return redirect("completion:detail", public_id=case.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update({"form": form, "title": f"Authorize exception: {requirement.title}"})
    return _render(request, "tvg_peoplehub/form.html", context)


@login_required
@require_POST
def administrative_closure(request, public_id):
    _viewer, _context, case = _case(request, public_id)
    confirm_administrative_closure(user=request.user, case=case)
    return redirect("completion:detail", public_id=case.public_id)


@login_required
@require_POST
def completion_finish(request, public_id):
    _viewer, _context, case = _case(request, public_id)
    complete_internship(user=request.user, case=case)
    return redirect("completion:detail", public_id=case.public_id)
