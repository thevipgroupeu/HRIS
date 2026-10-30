"""End-to-end smoke path on an empty synthetic company."""

from __future__ import annotations

from importlib import import_module

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from horilla.testkit import make_company, make_employee, make_user
from tvg_analytics.metrics import _started_internships
from tvg_analytics.models import AnalyticsSnapshot
from tvg_onboarding.models import InternOnboarding

REPORTING = import_module(
    "tvg_analytics.migrations.0002_snapshot_revisions_reporting_view"
)


@override_settings(TIME_ZONE="Europe/Warsaw")
class AnalyticsSmokeTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Smoke Employer")
        self.user = make_user("synthetic.analyst")
        for codename in (
            "view_peopleops_analytics",
            "generate_analytics_snapshot",
            "export_analytics_snapshot",
        ):
            self.user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="tvg_analytics", codename=codename
                )
            )
        self.user = type(self.user)._base_manager.get(pk=self.user.pk)
        self.employee = make_employee(
            company=self.company,
            email="synthetic.analyst@example.invalid",
            user=self.user,
        )
        self.client.force_login(self.user)

    def test_dashboards_snapshot_export_and_reporting_view(self):
        for name in ("analytics:executive", "analytics:recruitment"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "Unique applicants")
        response = self.client.get(reverse("analytics:executive"))
        fields = response.context["snapshot_fields"]
        response = self.client.post(
            reverse("analytics:snapshot-create"), {**fields, "dashboard": "executive"}
        )
        self.assertEqual(response.status_code, 302)
        snapshot = AnalyticsSnapshot.all_objects.get()
        failed = list(
            snapshot.validation_results.filter(outcome="failed").values_list(
                "check_code", "detail"
            )
        )
        self.assertEqual(snapshot.status, "validated", failed)
        detail = self.client.get(
            reverse("analytics:snapshot-detail", args=[snapshot.public_id])
        )
        self.assertEqual(detail.status_code, 200)
        with self.settings(TVG_ANALYTICS_EXPORT_PROVIDER="reporting_view"):
            self.client.post(
                reverse("analytics:snapshot-export", args=[snapshot.public_id])
            )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "exported")
        with connection.cursor() as cursor:
            for statement in REPORTING.reporting_view_statements(connection.vendor):
                cursor.execute(statement)
            reporting_view = (
                REPORTING.POSTGRES_VIEW
                if connection.vendor == "postgresql"
                else REPORTING.SQLITE_VIEW
            )
            cursor.execute(
                f"SELECT metric_key, value, suppressed FROM {reporting_view}"
            )
            rows = cursor.fetchall()
        self.assertTrue(rows)

    def test_actual_start_query_never_substitutes_the_planned_date(self):
        query = _started_internships(InternOnboarding.all_objects.all()).query
        sql = str(query).upper()
        self.assertIn("DATE_JOINING", sql)
        self.assertIn("IS NOT NULL", sql)
        self.assertNotIn("COALESCE", sql)
