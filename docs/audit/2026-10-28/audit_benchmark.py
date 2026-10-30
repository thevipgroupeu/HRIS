"""Small synthetic query benchmark; deliberately NOT a capacity certification."""

import statistics
import time

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from horilla.testkit import make_company
from tvg_analytics.metrics import AnalyticsQuery, collect_metrics
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture


class SyntheticAnalyticsBenchmark(TestCase):
    def test_small_analytics_dataset_reconciles_and_records_cost(self):
        company = make_company("Synthetic Benchmark Employer")
        openings = [
            make_job_opening(
                company=company, venture=make_venture(company=company, name=name)
            )
            for name in ("FluxOps", "FindMyStay")
        ]
        for index in range(50):
            person = make_person(
                company=company, email=f"audit-{index}@example.invalid"
            )
            for opening in openings:
                make_application(opening=opening, person=person)
        today = timezone.localdate()
        query = AnalyticsQuery(
            company_id=company.pk,
            period_start=today,
            period_end=today,
            as_of_date=today,
        )
        collect_metrics(query)  # exclude warm-up from measured iterations
        elapsed, counts = [], []
        for _ in range(10):
            with CaptureQueriesContext(connection) as captured:
                started = time.perf_counter()
                points = collect_metrics(query)
                elapsed.append((time.perf_counter() - started) * 1000)
            counts.append(len(captured))
        scalar = {p["metric_key"]: p["value"] for p in points if not p["dimensions"]}
        # Keep reconciliation independent of performance measurements.
        self.assertEqual(scalar["applications_total"], 100)
        self.assertEqual(scalar["unique_persons"], 50)
        print(
            "AUDIT: single-process SQLite, 50 synthetic people/100 applications, 10 warmed analytics collections; "
            f"median_ms={statistics.median(elapsed):.2f}, max_ms={max(elapsed):.2f}, "
            f"sql_queries_min={min(counts)}, sql_queries_max={max(counts)}; "
            "no HTTP, concurrency, PostgreSQL, uploads, password hashing or production-capacity claim"
        )
