"""Service-level tests for the snapshot lifecycle: build, validate, export.

Every person, company and address here is synthetic. Exporters are the
in-process fakes from ``tvg_analytics.integrations``; nothing leaves the test
database.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError

from django.contrib.auth.models import Permission
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db.models import Max
from django.test import TestCase, override_settings
from django.utils import timezone

from horilla.horilla_middlewares import set_selected_company
from horilla.testkit import make_company, make_employee, make_user
from tvg_analytics.definitions import METRICS
from tvg_analytics.integrations import (
    AnalyticsBatch,
    AnalyticsExportRejected,
    ExportMetric,
    ExportResult,
    ReportingViewPublisher,
    SyntheticAnalyticsExporter,
    get_exporter,
)
from tvg_analytics.metrics import AnalyticsQuery
from tvg_analytics.metrics import collect_metrics as real_collect_metrics
from tvg_analytics.models import (
    AnalyticsExportAttempt,
    AnalyticsMetricPoint,
    AnalyticsSnapshot,
    AnalyticsValidationResult,
)
from tvg_analytics.services import (
    build_batch,
    build_snapshot,
    export_snapshot,
    idempotency_key,
    is_small_cell,
    min_cell_size,
    query_fingerprint,
    validate_snapshot,
)
from tvg_careers.models import Application
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_venture

Status = AnalyticsSnapshot.Status
CODENAMES = {
    "view": "view_peopleops_analytics",
    "generate": "generate_analytics_snapshot",
    "export": "export_analytics_snapshot",
}
COLLECT = "tvg_analytics.services.collect_metrics"


# ---------------------------------------------------------------------------
# Shared synthetic helpers (also imported by the other analytics test modules).
# ---------------------------------------------------------------------------


def grant(user, *roles):
    """Grant analytics permissions by role name and return a fresh user."""

    for role in roles:
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tvg_analytics", codename=CODENAMES[role]
            )
        )
    return type(user)._base_manager.get(pk=user.pk)


def make_actor(company, username, *roles, superuser=False):
    """A synthetic employee of ``company`` whose user holds ``roles``."""

    email = f"{username}@example.invalid"
    user = grant(make_user(username, email=email, is_superuser=superuser), *roles)
    return make_employee(
        company=company,
        email=email,
        first_name="Synthetic",
        last_name=username.split(".")[-1].title(),
        user=user,
    )


def recent_period(days_back=10, length=30):
    end = timezone.localdate() - timedelta(days=days_back)
    return end - timedelta(days=length - 1), end


def make_query(company, *, period=None, **filters):
    start, end = period or recent_period()
    return AnalyticsQuery(
        company_id=company.pk,
        period_start=start,
        period_end=end,
        as_of_date=end,
        **filters,
    )


def aware(day, hour=10):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def submit(opening, *, person=None, source="company_website", submitted_at, name):
    """A synthetic application with a controlled source and submission time."""

    application = make_application(opening=opening, person=person, name=name)
    Application.all_objects.filter(pk=application.pk).update(
        source=source, submitted_at=submitted_at
    )
    application.refresh_from_db()
    return application


def adjust(points, metric_key, dimensions=None, **fields):
    """Copy ``points`` with one point changed (or appended when missing)."""

    dimensions = dimensions or {}
    result, found = [], False
    for point in points:
        if point["metric_key"] == metric_key and point["dimensions"] == dimensions:
            point = {**point, **fields}
            found = True
        result.append(point)
    if not found:
        result.append(
            {
                "dashboard": METRICS[metric_key].dashboard,
                "metric_key": metric_key,
                "value": Decimal(0),
                "numerator": None,
                "denominator": None,
                "dimensions": dimensions,
                **fields,
            }
        )
    return result


def collecting(*changes):
    """A collect_metrics replacement applying ``(key, dims, fields)`` changes."""

    def collect(query):
        points = real_collect_metrics(query)
        for key, dimensions, fields in changes:
            points = adjust(points, key, dimensions, **fields)
        return points

    return collect


# Breaks the applications_by_source reconciliation on an empty company.
INCONSISTENT = ("applications_by_source", {"source": "linkedin"}, {"value": Decimal(2)})

# Small and large cells that keep every reconciliation check passing.
CELL_CHANGES = (
    (
        "recruitment_backlog_by_stage",
        {"stage": "awaiting_screening"},
        {"value": Decimal(3)},
    ),
    (
        "recruitment_backlog_by_stage",
        {"stage": "verification_in_progress"},
        {"value": Decimal(7)},
    ),
    ("workflow_failures", {"stage": "delivery"}, {"value": Decimal(2)}),
    (
        "verification_completion_rate",
        {},
        {"value": Decimal("50.00"), "numerator": Decimal(1), "denominator": Decimal(2)},
    ),
)


def fake_point(key, value, dimensions=None, numerator=None, denominator=None):
    return {
        "dashboard": METRICS[key].dashboard,
        "metric_key": key,
        "value": Decimal(value),
        "numerator": None if numerator is None else Decimal(numerator),
        "denominator": None if denominator is None else Decimal(denominator),
        "dimensions": dimensions or {},
    }


class _RecordingExporter:
    destination = "synthetic"

    def __init__(self):
        self.calls = []

    def export(self, *, batch, idempotency_key):
        self.calls.append((batch, idempotency_key))
        return ExportResult(external_reference=f"recorded:{idempotency_key}")


class _BrokenExporter:
    destination = "synthetic"

    def __init__(self, error):
        self.error = error

    def export(self, *, batch, idempotency_key):
        raise self.error


@override_settings(TIME_ZONE="Europe/Warsaw")
class SnapshotTestCase(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Analytics Employer")
        self.other = make_company("Synthetic Foreign Employer", hq=False)
        self.analyst = make_actor(
            self.company, "synthetic.snapshot.analyst", "view", "generate", "export"
        )
        self.query = make_query(self.company)

    def tearDown(self):
        set_selected_company(None)
        super().tearDown()

    def build(self, query=None, actor=None, rebuild=False, collect=None):
        query = query or self.query
        actor = actor or self.analyst
        if collect is None:
            return build_snapshot(query=query, actor=actor, rebuild=rebuild)
        with mock.patch(COLLECT, side_effect=collect):
            return build_snapshot(query=query, actor=actor, rebuild=rebuild)

    def failed_checks(self, snapshot):
        return set(
            AnalyticsValidationResult.all_objects.filter(
                snapshot=snapshot, outcome=AnalyticsValidationResult.Outcome.FAILED
            ).values_list("check_code", flat=True)
        )


class BuildAuthorizationTests(SnapshotTestCase):
    def test_build_requires_generate_permission(self):
        viewer = make_actor(self.company, "synthetic.snapshot.viewer", "view", "export")
        with self.assertRaises(PermissionDenied):
            build_snapshot(query=self.query, actor=viewer)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_build_requires_same_company(self):
        foreign_query = make_query(self.other)
        with self.assertRaises(PermissionDenied):
            build_snapshot(query=foreign_query, actor=self.analyst)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_superuser_of_another_company_is_refused(self):
        foreign_root = make_actor(self.other, "synthetic.snapshot.root", superuser=True)
        with self.assertRaises(PermissionDenied):
            build_snapshot(query=self.query, actor=foreign_root)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_superuser_of_the_same_company_may_build(self):
        root = make_actor(self.company, "synthetic.snapshot.localroot", superuser=True)
        snapshot = build_snapshot(query=self.query, actor=root)
        self.assertEqual(snapshot.status, Status.VALIDATED)
        self.assertEqual(snapshot.generated_by, root)


class BuildLifecycleTests(SnapshotTestCase):
    def test_build_validates_and_records_named_checks(self):
        snapshot = self.build()
        self.assertEqual(snapshot.status, Status.VALIDATED, snapshot.last_error)
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.schema_version, 2)
        self.assertIsNotNone(snapshot.validated_at)
        self.assertEqual(snapshot.last_error, "")
        self.assertEqual(snapshot.fingerprint, query_fingerprint(self.query))
        self.assertEqual(snapshot.filters, self.query.filter_dict)
        codes = set(
            AnalyticsValidationResult.all_objects.filter(snapshot=snapshot).values_list(
                "check_code", flat=True
            )
        )
        for code in (
            "point_count_matches_build",
            "metric_allowlist",
            "scalar_metrics_complete",
            "dimension_allowlist",
            "no_prohibited_tokens",
            "applications_by_source_reconciles",
            "applications_reconcile_independent_count",
        ):
            self.assertIn(code, codes)
        self.assertEqual(self.failed_checks(snapshot), set())
        stored = AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).count()
        self.assertEqual(stored, snapshot.source_counts["metric_points"])
        self.assertEqual(snapshot.source_counts["min_cell_size"], 5)

    def test_repeated_build_returns_the_current_snapshot(self):
        first = self.build()
        with mock.patch(COLLECT) as collect:
            again = build_snapshot(query=self.query, actor=self.analyst)
        collect.assert_not_called()
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(AnalyticsSnapshot.all_objects.count(), 1)

    def test_rebuild_creates_revision_two_and_supersedes_only_after_validation(self):
        first = self.build()
        observed = []

        def observe(query):
            observed.append(AnalyticsSnapshot.all_objects.get(pk=first.pk).status)
            return real_collect_metrics(query)

        second = self.build(rebuild=True, collect=observe)
        first.refresh_from_db()
        self.assertEqual(observed, [Status.VALIDATED])
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.status, Status.VALIDATED)
        self.assertEqual(first.status, Status.SUPERSEDED)
        self.assertEqual(second.fingerprint, first.fingerprint)
        self.assertEqual(
            build_snapshot(query=self.query, actor=self.analyst).pk, second.pk
        )

    def test_failed_rebuild_keeps_the_previous_revision_current(self):
        first = self.build()
        second = self.build(rebuild=True, collect=collecting(INCONSISTENT))
        first.refresh_from_db()
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.status, Status.FAILED)
        self.assertEqual(first.status, Status.VALIDATED)
        self.assertEqual(
            build_snapshot(query=self.query, actor=self.analyst).pk, first.pk
        )

    def test_failed_validation_records_named_checks_and_allows_retry(self):
        failed = self.build(collect=collecting(INCONSISTENT))
        self.assertEqual(failed.status, Status.FAILED)
        self.assertIsNone(failed.validated_at)
        self.assertIn("applications_by_source_reconciles", self.failed_checks(failed))
        self.assertIn("applications_by_source_reconciles", failed.last_error)
        self.assertTrue(failed.last_error.startswith("Failed checks:"))
        result = AnalyticsValidationResult.all_objects.get(
            snapshot=failed, check_code="applications_by_source_reconciles"
        )
        self.assertEqual(result.expected_value, Decimal(0))
        self.assertEqual(result.actual_value, Decimal(2))
        # Passing checks are recorded too, so reviewers see the full picture.
        self.assertTrue(
            AnalyticsValidationResult.all_objects.filter(
                snapshot=failed, outcome=AnalyticsValidationResult.Outcome.PASSED
            ).exists()
        )

        retry = self.build()
        failed.refresh_from_db()
        self.assertEqual(retry.revision, 2)
        self.assertEqual(retry.status, Status.VALIDATED)
        self.assertEqual(failed.status, Status.FAILED)

    def test_collection_error_marks_snapshot_failed_without_points(self):
        error = RuntimeError("synthetic internal detail that must not be stored")
        with self.assertLogs("tvg_analytics.services", level="ERROR"):
            snapshot = self.build(collect=mock.Mock(side_effect=error))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, Status.FAILED)
        self.assertIn("RuntimeError", snapshot.last_error)
        self.assertNotIn("synthetic internal detail", snapshot.last_error)
        self.assertFalse(
            AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).exists()
        )
        self.assertFalse(
            AnalyticsValidationResult.all_objects.filter(snapshot=snapshot).exists()
        )
        retry = self.build()
        self.assertEqual((retry.revision, retry.status), (2, Status.VALIDATED))

    def test_concurrent_creation_returns_the_existing_validated_revision(self):
        first = self.build()
        # Both requests read the same last revision before either inserted.
        # Depending on timing, the competing row is rejected by save()'s
        # full_clean() (already committed) or by the database constraint.
        for path in ("validation", "integrity"):
            with self.subTest(path=path), ExitStack() as stack:
                stack.enter_context(
                    mock.patch(
                        "tvg_analytics.services.Max",
                        side_effect=lambda field: Max(field) - 1,
                    )
                )
                collect = stack.enter_context(mock.patch(COLLECT))
                if path == "integrity":
                    # The competing row is not yet visible to full_clean().
                    stack.enter_context(
                        mock.patch.object(AnalyticsSnapshot, "validate_constraints")
                    )
                again = build_snapshot(
                    query=self.query, actor=self.analyst, rebuild=True
                )
                collect.assert_not_called()
                self.assertEqual(again.pk, first.pk)
                self.assertEqual(again.status, Status.VALIDATED)
                self.assertEqual(AnalyticsSnapshot.all_objects.count(), 1)

    def _building_row(self):
        return AnalyticsSnapshot.all_objects.create(
            company=self.company,
            period_start=self.query.period_start,
            period_end=self.query.period_end,
            as_of_date=self.query.as_of_date,
            filters=self.query.filter_dict,
            fingerprint=query_fingerprint(self.query),
            revision=1,
            generated_by=self.analyst,
        )

    def test_concurrent_creation_completes_an_unbuilt_existing_revision(self):
        concurrent = self._building_row()
        with mock.patch(
            "tvg_analytics.services.Max", side_effect=lambda field: Max(field) - 1
        ):
            snapshot = build_snapshot(query=self.query, actor=self.analyst)
        self.assertEqual(snapshot.pk, concurrent.pk)
        self.assertEqual(snapshot.status, Status.VALIDATED)
        self.assertEqual(AnalyticsSnapshot.all_objects.count(), 1)

    def test_concurrent_creation_does_not_rebuild_a_revision_in_progress(self):
        concurrent = self._building_row()
        AnalyticsMetricPoint.all_objects.create(
            snapshot=concurrent,
            dashboard="executive",
            metric_key="applications_total",
            period_start=self.query.period_start,
            period_end=self.query.period_end,
            value=Decimal(0),
            dimension_key="synthetic",
        )
        with mock.patch(
            "tvg_analytics.services.Max", side_effect=lambda field: Max(field) - 1
        ), mock.patch(COLLECT) as collect:
            snapshot = build_snapshot(query=self.query, actor=self.analyst)
        collect.assert_not_called()
        self.assertEqual(snapshot.pk, concurrent.pk)
        self.assertEqual(snapshot.status, Status.BUILDING)
        self.assertEqual(
            AnalyticsMetricPoint.all_objects.filter(snapshot=concurrent).count(), 1
        )

    def test_build_reads_through_all_objects_regardless_of_selected_company(self):
        opening = make_job_opening(company=self.company)
        for day, name in (
            (2, "Synthetic Applicant Alpha"),
            (4, "Synthetic Applicant Beta"),
        ):
            submit(
                opening,
                source="linkedin",
                submitted_at=aware(self.query.period_start + timedelta(days=day)),
                name=name,
            )
        expected = {
            (p["metric_key"], json.dumps(p["dimensions"], sort_keys=True)): p["value"]
            for p in real_collect_metrics(self.query)
        }
        set_selected_company(self.other.pk)
        snapshot = build_snapshot(query=self.query, actor=self.analyst)
        self.assertEqual(snapshot.status, Status.VALIDATED, snapshot.last_error)
        stored = {
            (p.metric_key, json.dumps(p.dimensions, sort_keys=True)): p.value
            for p in AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot)
        }
        self.assertEqual(stored, expected)
        self.assertEqual(stored[("applications_total", "{}")], 2)
        attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.SUCCEEDED)
        self.assertEqual(snapshot.status, Status.EXPORTED)


class StatusGuardTests(SnapshotTestCase):
    def test_failed_snapshot_cannot_become_validated(self):
        snapshot = self.build(collect=collecting(INCONSISTENT))
        snapshot.status = Status.VALIDATED
        with self.assertRaises(ValidationError):
            snapshot.save()
        self.assertEqual(
            AnalyticsSnapshot.all_objects.get(pk=snapshot.pk).status, Status.FAILED
        )

    def test_status_only_moves_forward(self):
        snapshot = self.build()
        for illegal in (Status.BUILDING, Status.FAILED):
            snapshot.refresh_from_db()
            snapshot.status = illegal
            with self.assertRaises(ValidationError, msg=illegal):
                snapshot.save()
        export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, Status.EXPORTED)
        snapshot.status = Status.VALIDATED
        with self.assertRaises(ValidationError):
            snapshot.save()
        snapshot.refresh_from_db()
        snapshot.status = Status.SUPERSEDED
        snapshot.save()
        snapshot.status = Status.EXPORTED
        with self.assertRaises(ValidationError):
            snapshot.save()

    def test_frozen_fields_cannot_change(self):
        snapshot = self.build()
        changes = {
            "company_id": self.other.pk,
            "filters": {**snapshot.filters, "source": "linkedin"},
            "period_start": snapshot.period_start - timedelta(days=1),
            "as_of_date": snapshot.as_of_date + timedelta(days=1),
            "revision": 9,
            "fingerprint": "0" * 64,
        }
        for field, value in changes.items():
            fresh = AnalyticsSnapshot.all_objects.get(pk=snapshot.pk)
            setattr(fresh, field, value)
            with self.assertRaises(ValidationError) as caught:
                fresh.save()
            self.assertIn(field, " ".join(caught.exception.messages))
        stored = AnalyticsSnapshot.all_objects.get(pk=snapshot.pk)
        self.assertEqual(stored.company_id, self.company.pk)
        self.assertEqual(stored.filters, snapshot.filters)

    def test_metric_points_validation_results_and_attempts_are_immutable(self):
        snapshot = self.build()
        export_snapshot(snapshot=snapshot, actor=self.analyst)
        point = AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).first()
        point.value = Decimal(99)
        result = AnalyticsValidationResult.all_objects.filter(snapshot=snapshot).first()
        result.outcome = AnalyticsValidationResult.Outcome.FAILED
        attempt = AnalyticsExportAttempt.all_objects.get(snapshot=snapshot)
        attempt.error_code = "synthetic_tamper"
        for record in (point, result, attempt):
            with self.assertRaises(ValidationError, msg=type(record).__name__):
                record.save()
        self.assertNotEqual(
            AnalyticsMetricPoint.all_objects.get(pk=point.pk).value, Decimal(99)
        )

    def test_validate_snapshot_refuses_snapshots_that_are_not_building(self):
        validated = self.build()
        failed = self.build(
            query=make_query(self.company, period=recent_period(days_back=40)),
            collect=collecting(INCONSISTENT),
        )
        for snapshot in (validated, failed):
            before = AnalyticsValidationResult.all_objects.filter(
                snapshot=snapshot
            ).count()
            with self.assertRaises(ValidationError):
                validate_snapshot(snapshot)
            self.assertEqual(
                AnalyticsValidationResult.all_objects.filter(snapshot=snapshot).count(),
                before,
            )

    def test_generator_must_belong_to_the_snapshot_company(self):
        foreign = make_actor(self.other, "synthetic.snapshot.foreign", "generate")
        with self.assertRaises(ValidationError):
            AnalyticsSnapshot.all_objects.create(
                company=self.company,
                period_start=self.query.period_start,
                period_end=self.query.period_end,
                as_of_date=self.query.as_of_date,
                fingerprint=query_fingerprint(self.query),
                generated_by=foreign,
            )


class SmallCellTests(SnapshotTestCase):
    def setUp(self):
        super().setUp()
        self.venture = make_venture(company=self.company)

    def test_dimensioned_counts_below_the_threshold_are_flagged(self):
        dims = {"source": "linkedin"}
        for value, flagged in (
            (1, True),
            (4, True),
            (5, False),
            (12, False),
            (0, False),
        ):
            point = fake_point("applications_by_source", value, dims)
            self.assertIs(is_small_cell(point, self.query, 5), flagged, value)

    def test_scalar_counts_are_flagged_only_under_a_narrowing_filter(self):
        point = fake_point("applications_total", 3)
        self.assertFalse(is_small_cell(point, self.query, 5))
        for filters in (
            {"venture_id": self.venture.pk},
            {"department_id": 1},
            {"role_id": 1},
            {"source": "linkedin"},
        ):
            narrowed = make_query(self.company, **filters)
            self.assertTrue(is_small_cell(point, narrowed, 5), filters)
            self.assertFalse(
                is_small_cell(fake_point("applications_total", 0), narrowed, 5), filters
            )

    def test_rates_and_durations_use_their_denominator(self):
        rate = "verification_completion_rate"
        self.assertTrue(
            is_small_cell(fake_point(rate, "50.00", None, 1, 2), self.query, 5)
        )
        self.assertFalse(
            is_small_cell(fake_point(rate, "40.00", None, 2, 5), self.query, 5)
        )
        self.assertFalse(
            is_small_cell(fake_point(rate, "0", None, 0, 0), self.query, 5)
        )
        duration = "stage_average_duration_days"
        stage = {"stage": "invitation_to_booking"}
        self.assertTrue(
            is_small_cell(fake_point(duration, 3, stage, 6, 2), self.query, 5)
        )
        self.assertFalse(
            is_small_cell(fake_point(duration, 0, stage, 0, 0), self.query, 5)
        )

    def test_system_event_counts_are_never_flagged(self):
        point = fake_point("workflow_failures", 2, {"stage": "delivery"})
        self.assertFalse(is_small_cell(point, self.query, 5))
        narrowed = make_query(self.company, venture_id=self.venture.pk)
        self.assertFalse(is_small_cell(point, narrowed, 5))

    def test_threshold_comes_from_settings(self):
        self.assertEqual(min_cell_size(), 5)
        with self.settings(TVG_ANALYTICS_MIN_CELL_SIZE=3):
            self.assertEqual(min_cell_size(), 3)
        with self.settings(TVG_ANALYTICS_MIN_CELL_SIZE=0):
            self.assertEqual(min_cell_size(), 1)

    def _flags(self, snapshot):
        return {
            (p.metric_key, p.dimensions.get("stage", "")): p.is_small_cell
            for p in AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot)
        }

    def test_build_stores_small_cell_flags(self):
        snapshot = self.build(collect=collecting(*CELL_CHANGES))
        self.assertEqual(snapshot.status, Status.VALIDATED, snapshot.last_error)
        flags = self._flags(snapshot)
        self.assertTrue(flags[("recruitment_backlog_by_stage", "awaiting_screening")])
        self.assertFalse(
            flags[("recruitment_backlog_by_stage", "verification_in_progress")]
        )
        self.assertFalse(
            flags[("recruitment_backlog_by_stage", "awaiting_letter_receipt")]
        )
        self.assertFalse(flags[("workflow_failures", "delivery")])
        self.assertTrue(flags[("verification_completion_rate", "")])
        self.assertFalse(flags[("applications_total", "")])

    @override_settings(TVG_ANALYTICS_MIN_CELL_SIZE=3)
    def test_build_uses_the_configured_threshold(self):
        snapshot = self.build(collect=collecting(*CELL_CHANGES))
        flags = self._flags(snapshot)
        self.assertEqual(snapshot.source_counts["min_cell_size"], 3)
        self.assertFalse(flags[("recruitment_backlog_by_stage", "awaiting_screening")])
        self.assertTrue(flags[("verification_completion_rate", "")])


class ExportTests(SnapshotTestCase):
    def attempts(self, snapshot):
        return list(
            AnalyticsExportAttempt.all_objects.filter(snapshot=snapshot).order_by(
                "sequence"
            )
        )

    def test_export_requires_export_permission(self):
        snapshot = self.build()
        generator = make_actor(
            self.company, "synthetic.snapshot.generator", "view", "generate"
        )
        with self.assertRaises(PermissionDenied):
            export_snapshot(snapshot=snapshot, actor=generator)
        self.assertEqual(self.attempts(snapshot), [])

    def test_export_requires_the_same_company(self):
        snapshot = self.build()
        for actor in (
            make_actor(self.other, "synthetic.snapshot.publisher", "export"),
            make_actor(self.other, "synthetic.snapshot.foreignroot", superuser=True),
        ):
            with self.assertRaises(PermissionDenied):
                export_snapshot(snapshot=snapshot, actor=actor)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, Status.VALIDATED)
        self.assertEqual(self.attempts(snapshot), [])

    def test_only_validated_snapshots_are_exportable(self):
        failed = self.build(collect=collecting(INCONSISTENT))
        with self.assertRaises(ValidationError):
            export_snapshot(snapshot=failed, actor=self.analyst)
        first = self.build(query=make_query(self.company, source="linkedin"))
        self.build(query=make_query(self.company, source="linkedin"), rebuild=True)
        first.refresh_from_db()
        self.assertEqual(first.status, Status.SUPERSEDED)
        with self.assertRaises(ValidationError):
            export_snapshot(snapshot=first, actor=self.analyst)
        self.assertFalse(AnalyticsExportAttempt.all_objects.exists())

    @override_settings(TVG_ANALYTICS_EXPORT_PROVIDER="disabled")
    def test_disabled_exporter_records_a_failed_attempt(self):
        snapshot = self.build()
        attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.FAILED)
        self.assertEqual(attempt.error_code, "analytics_export_disabled")
        self.assertEqual(attempt.destination, "disabled")
        self.assertEqual(attempt.sequence, 1)
        self.assertEqual(
            attempt.idempotency_key, f"{idempotency_key(snapshot)}:attempt:1"
        )
        self.assertEqual(attempt.attempted_by, self.analyst)
        self.assertEqual(snapshot.status, Status.VALIDATED)
        self.assertIsNone(snapshot.exported_at)

    def test_retry_after_failure_increments_the_sequence_then_succeeds(self):
        snapshot = self.build()
        with self.settings(TVG_ANALYTICS_EXPORT_PROVIDER="disabled"):
            first = export_snapshot(snapshot=snapshot, actor=self.analyst)
            second = export_snapshot(snapshot=snapshot, actor=self.analyst)
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        success = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(success.sequence, 3)
        self.assertEqual(success.outcome, AnalyticsExportAttempt.Outcome.SUCCEEDED)
        self.assertEqual(success.destination, "synthetic")
        self.assertEqual(success.idempotency_key, idempotency_key(snapshot))
        self.assertEqual(
            success.external_reference, f"synthetic:{idempotency_key(snapshot)}"
        )
        self.assertEqual(snapshot.status, Status.EXPORTED)
        self.assertIsNotNone(snapshot.exported_at)

    @override_settings(TVG_ANALYTICS_EXPORT_PROVIDER="reporting_view")
    def test_reporting_view_publication_and_idempotent_repeat(self):
        snapshot = self.build()
        attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.SUCCEEDED)
        self.assertEqual(attempt.destination, "reporting_view")
        self.assertEqual(
            attempt.external_reference,
            f"tvg_analytics_reporting.metric_point_v1:{snapshot.public_id}",
        )
        self.assertEqual(snapshot.status, Status.EXPORTED)
        exported_at = snapshot.exported_at

        with mock.patch("tvg_analytics.services.get_exporter") as exporter:
            repeat = export_snapshot(snapshot=snapshot, actor=self.analyst)
        exporter.assert_not_called()
        snapshot.refresh_from_db()
        self.assertEqual(repeat.pk, attempt.pk)
        self.assertEqual(len(self.attempts(snapshot)), 1)
        self.assertEqual(snapshot.exported_at, exported_at)

    def test_exporter_receives_the_idempotency_key_and_a_minimised_batch(self):
        snapshot = self.build(collect=collecting(*CELL_CHANGES))
        recorder = _RecordingExporter()
        with mock.patch("tvg_analytics.services.get_exporter", return_value=recorder):
            export_snapshot(snapshot=snapshot, actor=self.analyst)
        [(batch, key)] = recorder.calls
        self.assertEqual(key, f"analytics:{snapshot.public_id}:v2")
        self.assertEqual(batch.snapshot_id, str(snapshot.public_id))
        self.assertEqual(batch.company_id, self.company.pk)
        self.assertEqual(
            len(batch.metrics),
            AnalyticsMetricPoint.all_objects.filter(snapshot=snapshot).count(),
        )

    def test_exporting_revision_two_supersedes_exported_revision_one(self):
        first = self.build()
        export_snapshot(snapshot=first, actor=self.analyst)
        second = self.build(rebuild=True)
        first.refresh_from_db()
        # An exported revision stays published until its replacement is.
        self.assertEqual(first.status, Status.EXPORTED)
        self.assertEqual(second.status, Status.VALIDATED)
        export_snapshot(snapshot=second, actor=self.analyst)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Status.SUPERSEDED)
        self.assertEqual(second.status, Status.EXPORTED)

    def test_unexpected_exporter_error_is_recorded_by_class_name(self):
        snapshot = self.build()
        broken = _BrokenExporter(ValueError("synthetic raw detail with a token"))
        with mock.patch(
            "tvg_analytics.services.get_exporter", return_value=broken
        ), self.assertLogs("tvg_analytics.services", level="ERROR"):
            attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.FAILED)
        self.assertEqual(attempt.error_code, "analytics_export_failed")
        self.assertEqual(attempt.error_message, "ValueError")
        self.assertNotIn("synthetic raw detail", attempt.error_message)
        self.assertEqual(snapshot.status, Status.VALIDATED)

    def test_exporter_error_with_a_non_text_code_is_still_recorded(self):
        # urllib's HTTPError carries an integer ``code``; it must not crash the
        # attempt bookkeeping or leak into the recorded error code.
        snapshot = self.build()
        error = HTTPError(
            "https://reporting.invalid/", 503, "Synthetic outage", None, None
        )
        broken = _BrokenExporter(error)
        with mock.patch(
            "tvg_analytics.services.get_exporter", return_value=broken
        ), self.assertLogs("tvg_analytics.services", level="ERROR"):
            attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.FAILED)
        self.assertEqual(attempt.error_code, "analytics_export_failed")
        self.assertEqual(attempt.error_message, "HTTPError")

    def test_synthetic_exporter_is_refused_in_production(self):
        for environment in ("production", "staging"):
            with self.settings(HORILLA_ENV=environment), self.assertRaises(
                ImproperlyConfigured
            ):
                get_exporter()
        with self.settings(HORILLA_ENV="", TVG_AUTOMATION_ENVIRONMENT="production"):
            with self.assertRaises(ImproperlyConfigured):
                get_exporter()
        with self.settings(
            HORILLA_ENV="production", TVG_ANALYTICS_EXPORT_PROVIDER="reporting_view"
        ):
            self.assertIsInstance(get_exporter(), ReportingViewPublisher)
        with self.settings(TVG_ANALYTICS_EXPORT_PROVIDER="synthetic-unknown"):
            with self.assertRaises(ImproperlyConfigured):
                get_exporter()

    def test_export_under_production_with_synthetic_provider_records_failure(self):
        snapshot = self.build()
        with self.settings(HORILLA_ENV="production"), self.assertLogs(
            "tvg_analytics.services", level="ERROR"
        ):
            attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.FAILED)
        self.assertEqual(attempt.destination, "unconfigured")
        self.assertEqual(attempt.error_message, "ImproperlyConfigured")
        self.assertEqual(snapshot.status, Status.VALIDATED)

    def test_batch_suppresses_small_cells_and_serialises_decimals_as_text(self):
        snapshot = self.build(collect=collecting(*CELL_CHANGES))
        batch = build_batch(snapshot)
        by_key = {
            (m.metric_key, m.dimensions.get("stage", "")): m for m in batch.metrics
        }
        small = by_key[("recruitment_backlog_by_stage", "awaiting_screening")]
        self.assertTrue(small.suppressed)
        self.assertEqual((small.value, small.numerator, small.denominator), (None,) * 3)
        rate = by_key[("verification_completion_rate", "")]
        self.assertTrue(rate.suppressed)
        self.assertEqual((rate.value, rate.numerator, rate.denominator), (None,) * 3)
        large = by_key[("recruitment_backlog_by_stage", "verification_in_progress")]
        self.assertFalse(large.suppressed)
        self.assertEqual(large.value, Decimal(7))

        payload = batch.to_payload()
        decoded = json.loads(json.dumps(payload))
        self.assertEqual(decoded["schema_version"], 2)
        self.assertEqual(decoded["snapshot_id"], str(snapshot.public_id))
        self.assertEqual(decoded["filters"], snapshot.filters)
        values = {
            (m["metric_key"], m["dimensions"].get("stage", "")): m
            for m in decoded["metrics"]
        }
        large_payload = values[
            ("recruitment_backlog_by_stage", "verification_in_progress")
        ]
        self.assertIsInstance(large_payload["value"], str)
        self.assertEqual(Decimal(large_payload["value"]), Decimal(7))
        self.assertIsNone(
            values[("recruitment_backlog_by_stage", "awaiting_screening")]["value"]
        )
        for metric in decoded["metrics"]:
            for field in ("value", "numerator", "denominator"):
                self.assertNotIsInstance(metric[field], float)

    def test_exporters_refuse_batches_with_identifier_dimensions(self):
        batch = AnalyticsBatch(
            schema_version=2,
            snapshot_id="synthetic",
            company_id=self.company.pk,
            as_of_date="2026-01-31",
            filters={},
            metrics=(
                ExportMetric(
                    dashboard="recruitment",
                    metric_key="applications_by_source",
                    period_start="2026-01-01",
                    period_end="2026-01-31",
                    value=Decimal(9),
                    numerator=None,
                    denominator=None,
                    dimensions={"candidate_name": "Synthetic Candidate"},
                ),
            ),
        )
        for exporter in (SyntheticAnalyticsExporter(), ReportingViewPublisher()):
            with self.assertRaises(AnalyticsExportRejected):
                exporter.export(batch=batch, idempotency_key="synthetic-key")

    def test_export_reads_through_all_objects_regardless_of_selected_company(self):
        snapshot = self.build()
        set_selected_company(self.other.pk)
        attempt = export_snapshot(snapshot=snapshot, actor=self.analyst)
        snapshot.refresh_from_db()
        self.assertEqual(attempt.outcome, AnalyticsExportAttempt.Outcome.SUCCEEDED)
        self.assertEqual(snapshot.status, Status.EXPORTED)


# Re-exported helpers for the sibling test modules.
__all__ = [
    "CELL_CHANGES",
    "COLLECT",
    "INCONSISTENT",
    "aware",
    "collecting",
    "grant",
    "make_actor",
    "make_query",
    "recent_period",
    "submit",
]
