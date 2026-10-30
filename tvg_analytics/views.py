from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET, require_POST

from horilla.horilla_middlewares import get_selected_company

from .definitions import METRICS
from .forms import AnalyticsFilterForm, query_params, query_string
from .metrics import collect_metrics
from .models import (
    AnalyticsExportAttempt,
    AnalyticsMetricPoint,
    AnalyticsSnapshot,
    AnalyticsValidationResult,
)
from .presentation import build_dashboard, dimension_label
from .reports import REPORT_SLUGS
from .services import (
    EXPORT_PERMISSION,
    GENERATE_PERMISSION,
    VIEW_PERMISSION,
    build_snapshot,
    export_snapshot,
    query_fingerprint,
    user_can,
)

DASHBOARD_URLS = {
    "executive": "analytics:executive",
    "recruitment": "analytics:recruitment",
}


def _analytics_actor(request, permission):
    """Return the requesting employee and the company whose data they may see.

    Analytics is scoped to the employee's own company. When Horilla has a
    different single company selected, refuse instead of mixing a permission
    resolved for one company with data from another.
    """

    if not user_can(request.user, permission):
        raise PermissionDenied("Missing analytics permission.")
    employee = getattr(request.user, "employee_get", None)
    company = employee.get_company() if employee else None
    if not company:
        raise PermissionDenied("A company-scoped employee record is required.")
    selected = get_selected_company()
    if selected not in (None, "", "all") and str(selected) != str(company.pk):
        raise PermissionDenied(
            "Switch the selected company to your employing company to view analytics."
        )
    return employee, company


def _horilla_reports(user, company_id):
    try:
        import report.metrics  # noqa: F401  (loads Horilla's built-in reports)
        from report.access import user_can_view_report
        from report.registry import get_report
    except ImportError:
        return []
    links = []
    for slug in (
        "recruitment-funnel",
        REPORT_SLUGS["executive"],
        REPORT_SLUGS["recruitment"],
    ):
        definition = get_report(slug)
        if not definition or not user_can_view_report(
            user, definition, company_id=company_id
        ):
            continue
        try:
            url = reverse("standard-report-detail", args=[slug])
        except NoReverseMatch:
            continue
        links.append({"label": definition.name, "url": url})
    return links


def _dashboard(request, dashboard):
    _employee, company = _analytics_actor(request, VIEW_PERMISSION)
    form = AnalyticsFilterForm(
        request.GET or AnalyticsFilterForm.default_data(), company=company
    )
    query = current = None
    sections = build_dashboard(dashboard, [])
    if form.is_valid():
        query = form.to_query(company.pk)
        sections = build_dashboard(dashboard, collect_metrics(query))
        current = (
            AnalyticsSnapshot.all_objects.filter(fingerprint=query_fingerprint(query))
            .order_by("-revision")
            .first()
        )
    context = {
        "dashboard": dashboard,
        "analytics_form": form,
        "query": query,
        "snapshot_fields": query_params(query) if query else {},
        "filter_querystring": query_string(query) if query else "",
        "current_snapshot": current,
        "snapshots": AnalyticsSnapshot.all_objects.filter(company=company)
        .exclude(status=AnalyticsSnapshot.Status.SUPERSEDED)
        .order_by("-generated_at", "-pk")[:10],
        "can_generate": user_can(request.user, GENERATE_PERMISSION),
        "can_export": user_can(request.user, EXPORT_PERMISSION),
        "horilla_reports": _horilla_reports(request.user, company.pk),
        **sections,
    }
    return render(request, "tvg_analytics/dashboard.html", context)


@login_required
@require_GET
def executive_dashboard(request):
    return _dashboard(request, "executive")


@login_required
@require_GET
def recruitment_dashboard(request):
    return _dashboard(request, "recruitment")


@login_required
@require_POST
def snapshot_create(request):
    employee, company = _analytics_actor(request, GENERATE_PERMISSION)
    form = AnalyticsFilterForm(request.POST, company=company)
    if not form.is_valid():
        return HttpResponseBadRequest(
            "Invalid analytics filters.", content_type="text/plain"
        )
    query = form.to_query(company.pk)
    snapshot = build_snapshot(
        query=query, actor=employee, rebuild=request.POST.get("rebuild") == "1"
    )
    if snapshot.status == AnalyticsSnapshot.Status.FAILED:
        messages.error(request, f"Snapshot failed validation. {snapshot.last_error}")
    else:
        messages.success(
            request,
            f"Snapshot revision {snapshot.revision} is {snapshot.get_status_display().lower()}.",
        )
    target = DASHBOARD_URLS.get(request.POST.get("dashboard"), "analytics:executive")
    return redirect(f"{reverse(target)}?{query_string(query)}")


@login_required
@require_GET
def snapshot_detail(request, public_id):
    _employee, company = _analytics_actor(request, VIEW_PERMISSION)
    snapshot = get_object_or_404(
        AnalyticsSnapshot.all_objects, public_id=public_id, company=company
    )
    points = list(
        AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).order_by(
            "dashboard", "metric_key", "pk"
        )
    )
    peers = defaultdict(list)
    for point in points:
        peers[point.metric_key].append(point.dimensions)
    rows = [
        {
            "label": (
                METRICS[point.metric_key].label
                if point.metric_key in METRICS
                else point.metric_key
            ),
            "dashboard": point.dashboard,
            "dimension": (
                dimension_label(point.dimensions, peers[point.metric_key])
                if point.dimensions
                else ""
            ),
            "point": point,
        }
        for point in points
    ]
    return render(
        request,
        "tvg_analytics/snapshot_detail.html",
        {
            "snapshot": snapshot,
            "rows": rows,
            "validation_results": AnalyticsValidationResult.all_objects.filter(
                snapshot=snapshot
            ).order_by("outcome", "check_code"),
            "export_attempts": AnalyticsExportAttempt.all_objects.filter(
                snapshot=snapshot
            ).order_by("sequence"),
            "can_export": user_can(request.user, EXPORT_PERMISSION),
        },
    )


@login_required
@require_POST
def snapshot_export(request, public_id):
    employee, company = _analytics_actor(request, EXPORT_PERMISSION)
    snapshot = get_object_or_404(
        AnalyticsSnapshot.all_objects, public_id=public_id, company=company
    )
    try:
        attempt = export_snapshot(snapshot=snapshot, actor=employee)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        if attempt.outcome == AnalyticsExportAttempt.Outcome.SUCCEEDED:
            messages.success(request, "Snapshot published to the reporting view.")
        else:
            messages.error(
                request,
                f"Export failed ({attempt.error_code}): {attempt.error_message}",
            )
    return redirect("analytics:snapshot-detail", public_id=snapshot.public_id)
