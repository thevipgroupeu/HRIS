from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from tvg_peoplehub.views import _base_context, _render

from .forms import MonthlyPresentationForm, ReviewForm, WeeklyReportForm
from .models import (
    MonthlyPresentation,
    MonthlyPresentationVersion,
    ReportStatus,
    WeeklyReport,
)
from .services import (
    can_review,
    can_view_item,
    company_id_for,
    management_summary,
    review_monthly_presentation,
    review_weekly_report,
    save_monthly_draft,
    save_weekly_draft,
    submit_monthly_presentation,
    submit_weekly_report,
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


@login_required
def reporting_home(request):
    employee, context = _base_context(request)
    context.update(
        {
            "weekly_reports": WeeklyReport.all_objects.filter(
                employee=employee
            ).prefetch_related("versions"),
            "presentations": MonthlyPresentation.all_objects.filter(
                employee=employee
            ).prefetch_related("versions"),
        }
    )
    return _render(request, "tvg_reporting/home.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def weekly_edit(request, public_id=None):
    employee, context = _base_context(request)
    report = None
    if public_id:
        report = get_object_or_404(WeeklyReport.all_objects, public_id=public_id)
        if report.employee_id != employee.pk:
            raise PermissionDenied("Only the intern can edit this report.")
        if report.status not in {ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED}:
            raise PermissionDenied("Submitted reports are read-only.")
    form = WeeklyReportForm(
        request.POST or None,
        employee=employee,
        report=report,
    )
    if request.method == "POST" and form.is_valid():
        try:
            report = save_weekly_draft(
                employee=employee,
                report=report,
                **{
                    key: value
                    for key, value in form.cleaned_data.items()
                    if key != "completed_task_records"
                },
            )
            report.completed_task_records.set(
                form.cleaned_data["completed_task_records"]
            )
            return redirect("reporting:weekly-detail", public_id=report.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update(
        {
            "form": form,
            "title": "Edit weekly report" if report else "New weekly report",
            "multipart": False,
        }
    )
    return _render(request, "tvg_reporting/form.html", context)


@login_required
def weekly_detail(request, public_id):
    viewer, context = _base_context(request)
    report = get_object_or_404(
        WeeklyReport.all_objects.select_related(
            "employee__employee_work_info__reporting_manager_id", "assigned_project"
        ).prefetch_related("versions__review"),
        public_id=public_id,
    )
    if not can_view_item(request.user, viewer, report.employee):
        raise PermissionDenied("You cannot view this report.")
    context.update(
        {
            "report": report,
            "versions": report.versions.all(),
            "can_review_current": can_review(
                request.user,
                viewer,
                report.employee,
                "tvg_reporting.review_weekly_report",
            ),
        }
    )
    return _render(request, "tvg_reporting/weekly_detail.html", context)


@login_required
@require_POST
def weekly_submit(request, public_id):
    employee, _ = _base_context(request)
    report = get_object_or_404(WeeklyReport.all_objects, public_id=public_id)
    try:
        submit_weekly_report(
            employee=employee,
            report=report,
            task_ids=report.completed_task_records.values_list("pk", flat=True),
        )
    except ValidationError as error:
        return HttpResponseBadRequest(
            " ".join(error.messages), content_type="text/plain"
        )
    return redirect("reporting:weekly-detail", public_id=report.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def presentation_edit(request, public_id=None):
    employee, context = _base_context(request)
    presentation = None
    if public_id:
        presentation = get_object_or_404(
            MonthlyPresentation.all_objects, public_id=public_id
        )
        if presentation.employee_id != employee.pk:
            raise PermissionDenied("Only the intern can edit this presentation.")
        if presentation.status not in {
            ReportStatus.DRAFT,
            ReportStatus.REVISION_REQUIRED,
        }:
            raise PermissionDenied("Submitted presentations are read-only.")
    form = MonthlyPresentationForm(
        request.POST or None,
        request.FILES or None,
        employee=employee,
        presentation=presentation,
    )
    if request.method == "POST" and form.is_valid():
        try:
            presentation = save_monthly_draft(
                employee=employee,
                presentation=presentation,
                presentation_month=form.cleaned_data["presentation_month"],
                assigned_project=form.cleaned_data["assigned_project"],
                presentation_date=form.cleaned_data["presentation_date"],
                project_milestones=form.cleaned_data["project_milestones"],
                file_metadata=form.cleaned_data["presentation_file"],
            )
            return redirect(
                "reporting:presentation-detail", public_id=presentation.public_id
            )
        except ValidationError as error:
            _form_error(form, error)
    context.update(
        {
            "form": form,
            "title": (
                "Edit monthly presentation"
                if presentation
                else "New monthly presentation"
            ),
            "multipart": True,
        }
    )
    return _render(request, "tvg_reporting/form.html", context)


@login_required
def presentation_detail(request, public_id):
    viewer, context = _base_context(request)
    presentation = get_object_or_404(
        MonthlyPresentation.all_objects.select_related(
            "employee__employee_work_info__reporting_manager_id", "assigned_project"
        ).prefetch_related("versions__review"),
        public_id=public_id,
    )
    if not can_view_item(request.user, viewer, presentation.employee):
        raise PermissionDenied("You cannot view this presentation.")
    context.update(
        {
            "presentation": presentation,
            "versions": presentation.versions.all(),
            "can_review_current": can_review(
                request.user,
                viewer,
                presentation.employee,
                "tvg_reporting.review_monthly_presentation",
            ),
        }
    )
    return _render(request, "tvg_reporting/presentation_detail.html", context)


@login_required
@require_POST
def presentation_submit(request, public_id):
    employee, _ = _base_context(request)
    presentation = get_object_or_404(
        MonthlyPresentation.all_objects, public_id=public_id
    )
    try:
        submit_monthly_presentation(employee=employee, presentation=presentation)
    except ValidationError as error:
        return HttpResponseBadRequest(
            " ".join(error.messages), content_type="text/plain"
        )
    return redirect("reporting:presentation-detail", public_id=presentation.public_id)


@login_required
def review_queue(request):
    reviewer, context = _base_context(request)
    company_id = company_id_for(reviewer)
    weekly = WeeklyReport.all_objects.filter(
        employee__employee_work_info__company_id_id=company_id,
        status=ReportStatus.SUBMITTED,
    ).select_related("employee")
    monthly = MonthlyPresentation.all_objects.filter(
        employee__employee_work_info__company_id_id=company_id,
        status=ReportStatus.SUBMITTED,
    ).select_related("employee")
    if not (
        request.user.is_superuser
        or request.user.has_perm("tvg_reporting.review_weekly_report")
    ):
        weekly = weekly.filter(
            employee__employee_work_info__reporting_manager_id=reviewer
        )
    if not (
        request.user.is_superuser
        or request.user.has_perm("tvg_reporting.review_monthly_presentation")
    ):
        monthly = monthly.filter(
            employee__employee_work_info__reporting_manager_id=reviewer
        )
    if not weekly.exists() and not monthly.exists():
        # A supervisor may legitimately have an empty queue; block unrelated employees.
        has_team = reviewer.reporting_manager.filter(is_active=True).exists()
        has_permission = request.user.has_perm(
            "tvg_reporting.review_weekly_report"
        ) or request.user.has_perm("tvg_reporting.review_monthly_presentation")
        if not (request.user.is_superuser or has_team or has_permission):
            raise PermissionDenied("Supervisor review access is required.")
    context.update({"weekly_reports": weekly, "presentations": monthly})
    return _render(request, "tvg_reporting/review_queue.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def weekly_review(request, public_id):
    reviewer, context = _base_context(request)
    report = get_object_or_404(
        WeeklyReport.all_objects.select_related("employee__employee_work_info"),
        public_id=public_id,
        status=ReportStatus.SUBMITTED,
    )
    if not can_review(
        request.user, reviewer, report.employee, "tvg_reporting.review_weekly_report"
    ):
        raise PermissionDenied("Supervisor review access is required.")
    version = report.versions.get(version_number=report.current_version)
    form = ReviewForm(request.POST or None, reviewer=reviewer)
    if request.method == "POST" and form.is_valid():
        try:
            review_weekly_report(
                user=request.user,
                version=version,
                outcome=form.cleaned_data["outcome"],
                comments=form.cleaned_data["comments"],
                task_completion_evidence=form.cleaned_data["evidence_review"],
                development_feedback=form.cleaned_data["development_feedback"],
                writing_quality_comments=form.cleaned_data["writing_quality_comments"],
                internship_performance_comments=form.cleaned_data[
                    "internship_performance_comments"
                ],
                rubric=form.cleaned_data["rubric"],
                rubric_scores=form.cleaned_data["rubric_scores"],
                reviewer_adjustments=form.cleaned_data["reviewer_adjustments"],
                adjustment_reason=form.cleaned_data["adjustment_reason"],
            )
            return redirect("reporting:weekly-detail", public_id=report.public_id)
        except ValidationError as error:
            _form_error(form, error)
    context.update({"form": form, "title": "Review weekly report", "multipart": False})
    return _render(request, "tvg_reporting/form.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def presentation_review(request, public_id):
    reviewer, context = _base_context(request)
    presentation = get_object_or_404(
        MonthlyPresentation.all_objects.select_related("employee__employee_work_info"),
        public_id=public_id,
        status=ReportStatus.SUBMITTED,
    )
    if not can_review(
        request.user,
        reviewer,
        presentation.employee,
        "tvg_reporting.review_monthly_presentation",
    ):
        raise PermissionDenied("Supervisor review access is required.")
    version = presentation.versions.get(version_number=presentation.current_version)
    form = ReviewForm(request.POST or None, reviewer=reviewer)
    if request.method == "POST" and form.is_valid():
        try:
            review_monthly_presentation(
                user=request.user,
                version=version,
                outcome=form.cleaned_data["outcome"],
                comments=form.cleaned_data["comments"],
                milestone_evidence=form.cleaned_data["evidence_review"],
                development_feedback=form.cleaned_data["development_feedback"],
                writing_quality_comments=form.cleaned_data["writing_quality_comments"],
                internship_performance_comments=form.cleaned_data[
                    "internship_performance_comments"
                ],
                rubric=form.cleaned_data["rubric"],
                rubric_scores=form.cleaned_data["rubric_scores"],
                reviewer_adjustments=form.cleaned_data["reviewer_adjustments"],
                adjustment_reason=form.cleaned_data["adjustment_reason"],
            )
            return redirect(
                "reporting:presentation-detail", public_id=presentation.public_id
            )
        except ValidationError as error:
            _form_error(form, error)
    context.update(
        {"form": form, "title": "Review monthly presentation", "multipart": False}
    )
    return _render(request, "tvg_reporting/form.html", context)


@login_required
def presentation_download(request, version_id):
    viewer, _ = _base_context(request)
    version = get_object_or_404(
        MonthlyPresentationVersion.all_objects.select_related("presentation__employee"),
        pk=version_id,
    )
    if not can_view_item(request.user, viewer, version.presentation.employee):
        raise PermissionDenied("You cannot download this presentation.")
    try:
        handle = version.presentation_file.open("rb")
    except (FileNotFoundError, OSError, ValueError) as error:
        raise Http404("Presentation file is unavailable.") from error
    response = FileResponse(
        handle,
        content_type=version.content_type,
        as_attachment=True,
        filename=version.original_filename,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def management(request):
    _, context = _base_context(request)
    context.update(management_summary(user=request.user))
    return _render(request, "tvg_reporting/management.html", context)
