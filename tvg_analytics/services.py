"""Snapshot lifecycle: build, validate, publish.

A snapshot freezes the aggregate metrics for one company, filter set and as-of
date. It is validated by automated reconciliation checks, and only a validated
snapshot can be exported (published) by a user holding the export permission.
The Metabase reporting view reads exported snapshots only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from tvg_careers.attribution import APPLICATION_SOURCES
from tvg_erasmus.models import ErasmusCaseDocument, ScreeningDecision
from tvg_offboarding.models import AccessRevocationTask
from tvg_onboarding.models import AccountProvisioningRequest

from .definitions import (
    FILTER_KEYS,
    HORIZON_DAYS,
    METRICS,
    PERSON_LINKED_UNITS,
    RECRUITMENT_BACKLOG_STAGES,
    RECRUITMENT_DURATION_STAGES,
    SCALAR_METRIC_KEYS,
    SCHEMA_VERSION,
    WORKFLOW_FAILURE_STAGES,
    looks_like_identifier,
    prohibited_tokens,
)
from .integrations import (
    AnalyticsBatch,
    AnalyticsExportError,
    ExportMetric,
    get_exporter,
)
from .metrics import AnalyticsQuery, collect_metrics, period_applications
from .models import (
    AnalyticsExportAttempt,
    AnalyticsMetricPoint,
    AnalyticsSnapshot,
    AnalyticsValidationResult,
)

logger = logging.getLogger(__name__)

VIEW_PERMISSION = "tvg_analytics.view_peopleops_analytics"
GENERATE_PERMISSION = "tvg_analytics.generate_analytics_snapshot"
EXPORT_PERMISSION = "tvg_analytics.export_analytics_snapshot"
ACTIVE_STATUSES = (
    AnalyticsSnapshot.Status.VALIDATED,
    AnalyticsSnapshot.Status.EXPORTED,
)
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}-01$")
CENT = Decimal("0.01")


def min_cell_size() -> int:
    return max(int(getattr(settings, "TVG_ANALYTICS_MIN_CELL_SIZE", 5)), 1)


def canonical_hash(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def query_fingerprint(query: AnalyticsQuery) -> str:
    return canonical_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "company_id": query.company_id,
            **query.filter_dict,
            "as_of_date": query.as_of_date.isoformat(),
        }
    )


def query_from_snapshot(snapshot) -> AnalyticsQuery:
    filters = snapshot.filters or {}
    return AnalyticsQuery(
        company_id=snapshot.company_id,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        as_of_date=snapshot.as_of_date,
        venture_id=filters.get("venture_id"),
        department_id=filters.get("department_id"),
        role_id=filters.get("role_id"),
        source=filters.get("source", ""),
    )


def user_can(user, permission) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.has_perm(permission))
    )


def _authorize(actor, permission, company_id):
    user = getattr(actor, "employee_user_id", None)
    if not user_can(user, permission):
        raise PermissionDenied("Missing analytics permission.")
    company = actor.get_company()
    if not company or company.pk != company_id:
        raise PermissionDenied("Analytics records are company-scoped.")


def is_small_cell(point: dict, query: AnalyticsQuery, threshold: int) -> bool:
    """Whether a value could single out an individual in an external report.

    Dimensioned counts, and any count of a narrowly filtered snapshot, are
    small when ``0 < value < threshold``. Rates and durations are small when
    their denominator is.
    """

    definition = METRICS[point["metric_key"]]
    if definition.unit in PERSON_LINKED_UNITS:
        if not (point["dimensions"] or query.has_narrowing_filter):
            return False
        return 0 < point["value"] < threshold
    if point["denominator"] is not None:
        return 0 < point["denominator"] < threshold
    return False


def latest_snapshot(fingerprint, statuses=ACTIVE_STATUSES):
    return (
        AnalyticsSnapshot.all_objects.filter(
            fingerprint=fingerprint, status__in=statuses
        )
        .order_by("-revision")
        .first()
    )


def _create_snapshot(query, actor, fingerprint):
    revision = (
        AnalyticsSnapshot.all_objects.filter(fingerprint=fingerprint).aggregate(
            last=Max("revision")
        )["last"]
        or 0
    ) + 1
    try:
        with transaction.atomic():
            return AnalyticsSnapshot.all_objects.create(
                company_id=query.company_id,
                period_start=query.period_start,
                period_end=query.period_end,
                as_of_date=query.as_of_date,
                filters=query.filter_dict,
                fingerprint=fingerprint,
                revision=revision,
                generated_by=actor,
            )
    except (IntegrityError, ValidationError):
        # A concurrent request created this revision first; reuse its result.
        # In autocommit mode a committed concurrent row is usually caught by
        # save()'s full_clean() (ValidationError) before the INSERT can raise
        # IntegrityError; any other validation error is re-raised.
        existing = AnalyticsSnapshot.all_objects.filter(
            fingerprint=fingerprint, revision=revision
        ).first()
        if existing is None:
            raise
        return existing


def build_snapshot(*, query: AnalyticsQuery, actor, rebuild=False):
    """Return the current validated snapshot, or build a new revision.

    A failed snapshot never blocks a retry, and ``rebuild`` creates a new
    revision even when a validated or exported one exists (for example after
    late data). Earlier revisions are superseded only once the replacement
    validates (and, for exported ones, only once it is exported).
    """

    _authorize(actor, GENERATE_PERMISSION, query.company_id)
    fingerprint = query_fingerprint(query)
    if not rebuild:
        existing = latest_snapshot(fingerprint)
        if existing:
            return existing
    snapshot = _create_snapshot(query, actor, fingerprint)
    if (
        snapshot.status != AnalyticsSnapshot.Status.BUILDING
        or AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).exists()
    ):
        return snapshot
    threshold = min_cell_size()
    try:
        with transaction.atomic():
            raw_points = collect_metrics(query)
            AnalyticsMetricPoint.all_objects.bulk_create(
                AnalyticsMetricPoint(
                    snapshot=snapshot,
                    dashboard=item["dashboard"],
                    metric_key=item["metric_key"],
                    period_start=query.period_start,
                    period_end=query.period_end,
                    value=item["value"],
                    numerator=item["numerator"],
                    denominator=item["denominator"],
                    dimensions=item["dimensions"],
                    dimension_key=canonical_hash(item["dimensions"]),
                    is_small_cell=is_small_cell(item, query, threshold),
                )
                for item in raw_points
            )
            snapshot.source_counts = {
                "metric_points": len(raw_points),
                "application_records": period_applications(
                    query, enforce_recruitment_company=False
                ).count(),
                "min_cell_size": threshold,
            }
            snapshot.save(update_fields=("source_counts",))
    except Exception as error:  # recorded on the snapshot, then surfaced to staff
        logger.exception("Analytics snapshot %s could not be built", snapshot.public_id)
        snapshot.status = AnalyticsSnapshot.Status.FAILED
        snapshot.last_error = f"Metric collection failed: {type(error).__name__}"[:500]
        snapshot.save(update_fields=("status", "last_error"))
        return snapshot
    validate_snapshot(snapshot)
    snapshot.refresh_from_db()
    if snapshot.status == AnalyticsSnapshot.Status.VALIDATED:
        for older in AnalyticsSnapshot.all_objects.filter(
            fingerprint=fingerprint,
            status=AnalyticsSnapshot.Status.VALIDATED,
            revision__lt=snapshot.revision,
        ):
            older.status = AnalyticsSnapshot.Status.SUPERSEDED
            older.save(update_fields=("status",))
    return snapshot


class _Checks:
    """Collects validation results; every check is named and recorded."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.results = []

    def add(self, code, passed, detail, expected=None, actual=None):
        self.results.append(
            AnalyticsValidationResult(
                snapshot=self.snapshot,
                check_code=code,
                outcome=(
                    AnalyticsValidationResult.Outcome.PASSED
                    if passed
                    else AnalyticsValidationResult.Outcome.FAILED
                ),
                expected_value=expected,
                actual_value=actual,
                detail=str(detail)[:500],
            )
        )

    def equal(self, code, expected, actual, detail):
        self.add(code, expected == actual, detail, expected=expected, actual=actual)

    def at_most(self, code, smaller, larger, detail):
        self.add(code, smaller <= larger, detail, expected=larger, actual=smaller)


def _allowed_dimension_values(snapshot):
    query = query_from_snapshot(snapshot)
    first_month = query.period_start.replace(day=1)

    def month_ok(value):
        if not MONTH_PATTERN.match(str(value)):
            return False
        return first_month <= date.fromisoformat(value) <= query.period_end

    statuses = {
        "documents_by_status": set(ErasmusCaseDocument.Status.values),
        "open_document_average_age_days": set(ErasmusCaseDocument.Status.values),
        "account_provisioning_by_status": set(AccountProvisioningRequest.Status.values),
        "access_revocation_by_status": set(AccessRevocationTask.Status.values),
    }
    stages = {
        "stage_average_duration_days": set(RECRUITMENT_DURATION_STAGES),
        "recruitment_backlog_by_stage": set(RECRUITMENT_BACKLOG_STAGES),
        "workflow_failures": set(WORKFLOW_FAILURE_STAGES),
    }

    def check(metric_key, key, value):
        if key == "source":
            return value in {code for code, _label in APPLICATION_SOURCES}
        if key == "screening_outcome":
            return value in set(ScreeningDecision.Status.values)
        if key == "month":
            return month_ok(value)
        if key == "horizon_days":
            return value in {str(days) for days in HORIZON_DAYS}
        if key == "status":
            return value in statuses.get(metric_key, set())
        if key == "stage":
            return value in stages.get(metric_key, set())
        if key.endswith("_id"):
            return value is None or isinstance(value, int)
        return isinstance(value, str) and not looks_like_identifier(value)

    return check


def validate_snapshot(snapshot):
    """Run automated reconciliation checks and set VALIDATED or FAILED.

    Funnel inequalities across stages (for example accepted <= offers) are
    deliberately not checked: with event-period counting a candidate can be
    offered in one period and accept in the next.
    """

    if snapshot.status != AnalyticsSnapshot.Status.BUILDING:
        raise ValidationError(
            "Only snapshots that are still building can be validated."
        )
    points = list(AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot))
    checks = _Checks(snapshot)
    by_key = defaultdict(list)
    for point in points:
        by_key[point.metric_key].append(point)

    def total(key, **dimensions):
        return sum(
            (
                point.value
                for point in by_key[key]
                if all(point.dimensions.get(k) == v for k, v in dimensions.items())
            ),
            Decimal(0),
        )

    expected_points = (snapshot.source_counts or {}).get("metric_points")
    checks.equal(
        "point_count_matches_build",
        Decimal(expected_points if expected_points is not None else -1),
        Decimal(len(points)),
        "Stored metric points must equal the number of collected points.",
    )
    unknown = sorted(set(by_key) - set(METRICS))
    checks.add(
        "metric_allowlist",
        not unknown,
        (
            f"Unsupported metric keys: {', '.join(unknown)}"
            if unknown
            else "All metric keys are approved."
        ),
    )
    missing = sorted(key for key in SCALAR_METRIC_KEYS if len(by_key[key]) != 1)
    checks.add(
        "scalar_metrics_complete",
        not missing,
        (
            f"Scalar metrics missing or duplicated: {', '.join(missing)}"
            if missing
            else "Every scalar metric appears exactly once."
        ),
    )
    bad_dimensions = sorted(
        {
            f"{point.metric_key}.{key}"
            for point in points
            if point.metric_key in METRICS
            for key in point.dimensions
            if key not in METRICS[point.metric_key].dimensions
        }
    )
    checks.add(
        "dimension_allowlist",
        not bad_dimensions,
        (
            f"Unsupported dimensions: {', '.join(bad_dimensions)}"
            if bad_dimensions
            else "All dimensions are approved aggregates."
        ),
    )
    invalid_filters = sorted(set(snapshot.filters or {}) - FILTER_KEYS)
    checks.add(
        "filter_allowlist",
        not invalid_filters,
        (
            f"Unsupported filters: {', '.join(invalid_filters)}"
            if invalid_filters
            else "All filters are approved."
        ),
    )
    exposed = sorted(
        {
            identifier
            for identifier in {
                *(key for point in points for key in point.dimensions),
                *(snapshot.filters or {}),
            }
            if prohibited_tokens(identifier)
        }
    )
    checks.add(
        "no_prohibited_tokens",
        not exposed,
        (
            f"Prohibited identifiers: {', '.join(exposed)}"
            if exposed
            else "No identity, document, medical or protected-characteristic fields."
        ),
    )
    value_ok = _allowed_dimension_values(snapshot)
    bad_values = sorted(
        {
            f"{point.metric_key}.{key}"
            for point in points
            for key, value in point.dimensions.items()
            if not value_ok(point.metric_key, key, value)
        }
    )
    checks.add(
        "dimension_values_allowed",
        not bad_values,
        (
            f"Unexpected dimension values in: {', '.join(bad_values)}"
            if bad_values
            else "Dimension values belong to their approved domains."
        ),
    )
    negative = sorted({point.metric_key for point in points if point.value < 0})
    checks.add(
        "values_non_negative",
        not negative,
        (
            f"Negative values: {', '.join(negative)}"
            if negative
            else "No metric is negative."
        ),
    )

    applications = total("applications_total")
    checks.at_most(
        "persons_not_greater_than_applications",
        total("unique_persons"),
        applications,
        "Unique persons may be fewer than applications but never more.",
    )
    independent = (snapshot.source_counts or {}).get("application_records")
    checks.equal(
        "applications_reconcile_independent_count",
        Decimal(independent if independent is not None else -1),
        applications,
        "Applications counted without the recruitment-company join must match; "
        "a difference means an opening's recruitment belongs to another company.",
    )
    for key in (
        "applications_by_source",
        "applications_by_venture",
        "applications_by_department",
        "applications_by_role",
        "applications_by_month",
    ):
        checks.equal(
            f"{key}_reconciles",
            applications,
            total(key),
            "Breakdown total must equal applications submitted.",
        )
    checks.at_most(
        "withdrawn_within_applications",
        total("applications_withdrawn"),
        applications,
        "Withdrawn applications are a subset of applications submitted.",
    )
    active = total("active_interns")
    for key in ("active_interns_by_venture", "active_interns_by_department"):
        checks.equal(
            f"{key}_reconciles",
            active,
            total(key),
            "Breakdown must equal active interns.",
        )
    checks.equal(
        "outstanding_tasks_reconcile",
        total("outstanding_onboarding_tasks"),
        total("outstanding_onboarding_tasks_by_item"),
        "Outstanding tasks by checklist item must equal the total.",
    )
    for key in ("upcoming_starts", "upcoming_completions"):
        series = [total(key, horizon_days=str(days)) for days in HORIZON_DAYS]
        checks.add(
            f"{key}_cumulative",
            all(a <= b for a, b in zip(series, series[1:])),
            "Cumulative horizons must not decrease from 7 to 90 days.",
        )
    checks.at_most(
        "upcoming_completions_within_active",
        total("upcoming_completions", horizon_days=str(HORIZON_DAYS[-1])),
        active,
        "Upcoming completions are a subset of active interns.",
    )
    checks.at_most(
        "awaiting_starts_within_planned",
        total("starts_awaiting_onboarding_completion"),
        total("planned_starts"),
        "Starts awaiting onboarding completion are a subset of planned starts.",
    )
    checks.at_most(
        "weekly_on_time_within_submitted",
        total("weekly_reports_on_time"),
        total("weekly_reports_submitted"),
        "On-time weekly reports are a subset of submitted reports.",
    )
    checks.at_most(
        "weekly_submitted_within_due",
        total("weekly_reports_submitted"),
        total("weekly_reports_due"),
        "Submitted weekly reports are a subset of reports due.",
    )
    checks.at_most(
        "monthly_completed_within_due",
        total("monthly_presentations_completed"),
        total("monthly_presentations_due"),
        "Approved presentations are a subset of presentations due.",
    )
    checks.at_most(
        "interviewed_within_completed_interviews",
        total("applications_interviewed"),
        total("interviews_completed"),
        "Each interviewed application has at least one completed interview.",
    )
    for point in points:
        definition = METRICS.get(point.metric_key)
        if not definition or point.denominator is None:
            continue
        label = point.metric_key + (
            f"__{point.dimensions.get('stage') or point.dimensions.get('status')}"
            if point.dimensions
            else ""
        )
        if definition.is_rate:
            expected = (
                (point.numerator * 100 / point.denominator).quantize(CENT)
                if point.denominator
                else Decimal(0)
            )
            passed = (
                point.numerator <= point.denominator
                and point.value == expected
                and 0 <= point.value <= 100
            )
            checks.add(
                f"{label}_rate_consistent",
                passed,
                "Rate must equal 100 x numerator / denominator and stay within 0-100.",
                expected=expected,
                actual=point.value,
            )
        elif definition.is_duration:
            expected = (
                (point.numerator / point.denominator).quantize(CENT)
                if point.denominator
                else Decimal(0)
            )
            checks.add(
                f"{label}_average_consistent",
                abs(point.value - expected) <= CENT,
                "Average must equal numerator / denominator.",
                expected=expected,
                actual=point.value,
            )

    AnalyticsValidationResult.all_objects.bulk_create(checks.results)
    failed = [
        result.check_code
        for result in checks.results
        if result.outcome == AnalyticsValidationResult.Outcome.FAILED
    ]
    snapshot.status = (
        AnalyticsSnapshot.Status.FAILED
        if failed
        else AnalyticsSnapshot.Status.VALIDATED
    )
    snapshot.validated_at = None if failed else timezone.now()
    snapshot.last_error = f"Failed checks: {', '.join(failed)}"[:500] if failed else ""
    snapshot.save(update_fields=("status", "validated_at", "last_error"))
    return not failed


def build_batch(snapshot) -> AnalyticsBatch:
    return AnalyticsBatch(
        schema_version=snapshot.schema_version,
        snapshot_id=str(snapshot.public_id),
        company_id=snapshot.company_id,
        as_of_date=snapshot.as_of_date.isoformat(),
        filters=dict(snapshot.filters),
        metrics=tuple(
            ExportMetric(
                dashboard=point.dashboard,
                metric_key=point.metric_key,
                period_start=point.period_start.isoformat(),
                period_end=point.period_end.isoformat(),
                value=None if point.is_small_cell else point.value,
                numerator=None if point.is_small_cell else point.numerator,
                denominator=None if point.is_small_cell else point.denominator,
                dimensions=dict(point.dimensions),
                suppressed=point.is_small_cell,
            )
            for point in AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot)
        ),
    )


def idempotency_key(snapshot) -> str:
    return f"analytics:{snapshot.public_id}:v{snapshot.schema_version}"


def _successful_attempt(snapshot):
    return AnalyticsExportAttempt.all_objects.filter(
        snapshot=snapshot, outcome=AnalyticsExportAttempt.Outcome.SUCCEEDED
    ).first()


def export_snapshot(*, snapshot, actor):
    """Publish a validated snapshot through the configured exporter.

    The exporter is called outside the database transaction so a slow or
    failing destination never holds the snapshot lock; every attempt is
    recorded, and a repeat after success returns the original attempt.
    """

    _authorize(actor, EXPORT_PERMISSION, snapshot.company_id)
    with transaction.atomic():
        locked = AnalyticsSnapshot.all_objects.select_for_update(of=("self",)).get(pk=snapshot.pk)
        existing = _successful_attempt(locked)
        if existing:
            return existing
        if locked.status != AnalyticsSnapshot.Status.VALIDATED:
            raise ValidationError("Only validated analytics snapshots may be exported.")
        batch = build_batch(locked)
        key = idempotency_key(locked)

    destination = "unconfigured"
    try:
        exporter = get_exporter()
        destination = exporter.destination
        result = exporter.export(batch=batch, idempotency_key=key)
        error = None
    except Exception as exc:  # every failure is recorded as an attempt
        if not isinstance(exc, AnalyticsExportError):
            logger.exception("Analytics export of %s failed", snapshot.public_id)
        result, error = None, exc

    with transaction.atomic():
        locked = AnalyticsSnapshot.all_objects.select_for_update(of=("self",)).get(pk=snapshot.pk)
        existing = _successful_attempt(locked)
        if existing:
            return existing
        sequence = (
            AnalyticsExportAttempt.all_objects.filter(snapshot=locked).aggregate(
                last=Max("sequence")
            )["last"]
            or 0
        ) + 1
        if error is not None:
            return AnalyticsExportAttempt.all_objects.create(
                snapshot=locked,
                sequence=sequence,
                destination=destination,
                outcome=AnalyticsExportAttempt.Outcome.FAILED,
                idempotency_key=f"{key}:attempt:{sequence}",
                # Only our own export errors carry a trusted text code; others
                # (e.g. urllib's HTTPError.code is an int) use the generic one.
                error_code=(
                    error.code
                    if isinstance(error, AnalyticsExportError)
                    and isinstance(getattr(error, "code", None), str)
                    else "analytics_export_failed"
                )[:60],
                error_message=(
                    str(error)
                    if isinstance(error, AnalyticsExportError)
                    else type(error).__name__
                )[:500],
                attempted_by=actor,
            )
        if locked.status != AnalyticsSnapshot.Status.VALIDATED:
            raise ValidationError("The snapshot changed state during export.")
        attempt = AnalyticsExportAttempt.all_objects.create(
            snapshot=locked,
            sequence=sequence,
            destination=destination,
            outcome=AnalyticsExportAttempt.Outcome.SUCCEEDED,
            idempotency_key=key,
            external_reference=result.external_reference[:180],
            attempted_by=actor,
        )
        for older in AnalyticsSnapshot.all_objects.filter(
            fingerprint=locked.fingerprint, status=AnalyticsSnapshot.Status.EXPORTED
        ).exclude(pk=locked.pk):
            older.status = AnalyticsSnapshot.Status.SUPERSEDED
            older.save(update_fields=("status",))
        locked.status = AnalyticsSnapshot.Status.EXPORTED
        locked.exported_at = timezone.now()
        locked.save(update_fields=("status", "exported_at"))
        return attempt
