"""Client-level tests for the PeopleOps analytics pages.

The default ROOT_URLCONF (``tvg_careers.peopleops_urls``) mounts the pages at
``/peopleops/analytics/``. All accounts and companies are synthetic.
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from horilla.horilla_middlewares import set_selected_company
from horilla.testkit import make_company, make_user
from report.models import ReportAccess
from tvg_analytics.definitions import BREAKDOWN_KEYS, CARD_KEYS, DETAIL_KEYS
from tvg_analytics.models import AnalyticsExportAttempt, AnalyticsSnapshot
from tvg_analytics.reports import REPORT_SLUGS
from tvg_analytics.services import build_snapshot
from tvg_analytics.tests.test_snapshots import (
    COLLECT,
    INCONSISTENT,
    collecting,
    grant,
    make_actor,
    make_query,
)
from tvg_careers.tests.factories import make_job_opening
from tvg_core.tests.factories import make_venture

Status = AnalyticsSnapshot.Status
EXECUTIVE = "analytics:executive"
RECRUITMENT = "analytics:recruitment"
CREATE = "analytics:snapshot-create"


def visible_text(html: str) -> str:
    """Text of the analytics <main> element without tags or attributes."""

    match = re.search(r'<main class="oh-wrapper tvg-analytics".*?</main>', html, re.S)
    body = match.group(0) if match else html
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


@override_settings(TIME_ZONE="Europe/Warsaw")
class AnalyticsViewTestCase(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic View Employer")
        self.other = make_company("Synthetic Foreign View Employer", hq=False)
        self.analyst = make_actor(
            self.company, "synthetic.view.analyst", "view", "generate", "export"
        )
        self.viewer = make_actor(self.company, "synthetic.view.viewer", "view")
        self.foreign_analyst = make_actor(
            self.other, "synthetic.view.foreign", "view", "generate", "export"
        )
        self.today = timezone.localdate()

    def tearDown(self):
        set_selected_company(None)
        super().tearDown()

    def login(self, employee):
        self.client.force_login(employee.employee_user_id)

    def dates(self, start, end):
        return {"period_start": start.isoformat(), "period_end": end.isoformat()}

    def snapshot(self, actor=None, collect=None, **filters):
        actor = actor or self.analyst
        query = make_query(actor.get_company(), **filters)
        if collect is None:
            return build_snapshot(query=query, actor=actor)
        with mock.patch(COLLECT, side_effect=collect):
            return build_snapshot(query=query, actor=actor)

    def messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]


class AccessTests(AnalyticsViewTestCase):
    def test_anonymous_users_are_redirected_to_login(self):
        for name in (EXECUTIVE, RECRUITMENT):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login", response["Location"])
        response = self.client.post(reverse(CREATE), {})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_users_without_view_permission_are_forbidden(self):
        snapshot = self.snapshot()
        outsider = make_actor(self.company, "synthetic.view.outsider")
        self.login(outsider)
        for name in (EXECUTIVE, RECRUITMENT):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        detail = reverse("analytics:snapshot-detail", args=[snapshot.public_id])
        self.assertEqual(self.client.get(detail).status_code, 403)
        export = reverse("analytics:snapshot-export", args=[snapshot.public_id])
        self.assertEqual(self.client.post(export).status_code, 403)
        self.assertFalse(AnalyticsExportAttempt.all_objects.exists())

    def test_permission_without_an_employee_record_is_forbidden(self):
        user = grant(
            make_user("synthetic.view.noemployee", email="noemployee@example.invalid"),
            "view",
            "generate",
        )
        self.client.force_login(user)
        for name in (EXECUTIVE, RECRUITMENT):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        response = self.client.post(
            reverse(CREATE), self.dates(self.today - timedelta(days=9), self.today)
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_selected_company_mismatch_is_forbidden(self):
        self.login(self.analyst)
        with mock.patch(
            "tvg_analytics.views.get_selected_company", return_value=self.other.pk
        ):
            self.assertEqual(self.client.get(reverse(EXECUTIVE)).status_code, 403)
            response = self.client.post(
                reverse(CREATE), self.dates(self.today - timedelta(days=9), self.today)
            )
            self.assertEqual(response.status_code, 403)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())
        for selected in ("all", self.company.pk, str(self.company.pk)):
            with mock.patch(
                "tvg_analytics.views.get_selected_company", return_value=selected
            ):
                self.assertEqual(
                    self.client.get(reverse(EXECUTIVE)).status_code, 200, selected
                )


class DashboardTests(AnalyticsViewTestCase):
    def test_dashboards_default_to_the_last_thirty_days(self):
        self.login(self.viewer)
        for name, dashboard in ((EXECUTIVE, "executive"), (RECRUITMENT, "recruitment")):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            query = response.context["query"]
            self.assertIsNotNone(query)
            self.assertEqual(query.period_start, self.today - timedelta(days=29))
            self.assertEqual(query.period_end, self.today)
            self.assertEqual(query.as_of_date, self.today)
            self.assertEqual(query.company_id, self.company.pk)
            self.assertTrue(response.context["analytics_form"].is_bound)
            self.assertEqual(response.context["dashboard"], dashboard)
            self.assertContains(response, "Unique applicants")
            self.assertContains(response, "Interviews booked")
            self.assertContains(response, 'class="tvg-card"')
            self.assertEqual(
                [card["key"] for card in response.context["cards"]],
                list(CARD_KEYS[dashboard]),
            )

    def test_cards_show_human_labels_not_metric_keys(self):
        self.login(self.viewer)
        for name, dashboard in ((EXECUTIVE, "executive"), (RECRUITMENT, "recruitment")):
            response = self.client.get(reverse(name))
            for card in response.context["cards"]:
                self.assertNotIn("_", card["label"], card["key"])
            text = visible_text(response.content.decode())
            headings = re.findall(
                r'<h3 id="card-[^"]+">([^<]*)</h3>', response.content.decode()
            )
            self.assertEqual(len(headings), len(CARD_KEYS[dashboard]))
            for heading in headings:
                self.assertNotIn("_", heading)
            for key in (
                *CARD_KEYS[dashboard],
                *DETAIL_KEYS[dashboard],
                *BREAKDOWN_KEYS[dashboard],
            ):
                self.assertNotIn(key, text, f"raw metric key {key} on {name}")

    def test_invalid_filters_render_errors_and_no_metric_cards(self):
        foreign_venture = make_venture(company=self.other)
        foreign_opening = make_job_opening(company=self.other, venture=foreign_venture)
        cases = {
            "end before start": (
                self.dates(
                    self.today - timedelta(days=5), self.today - timedelta(days=10)
                ),
                "cannot precede",
            ),
            "future end": (
                self.dates(
                    self.today - timedelta(days=5), self.today + timedelta(days=1)
                ),
                "cannot end in the future",
            ),
            "too long": (
                self.dates(self.today - timedelta(days=400), self.today),
                "limited to 366 days",
            ),
            "foreign venture": (
                {
                    **self.dates(self.today - timedelta(days=5), self.today),
                    "venture": foreign_venture.pk,
                },
                "Select a valid choice",
            ),
            "foreign department": (
                {
                    **self.dates(self.today - timedelta(days=5), self.today),
                    "department": foreign_opening.department.pk,
                },
                "Select a valid choice",
            ),
            "unknown source": (
                {
                    **self.dates(self.today - timedelta(days=5), self.today),
                    "source": "x",
                },
                "Select a valid choice",
            ),
        }
        self.login(self.analyst)
        for label, (params, message) in cases.items():
            response = self.client.get(reverse(EXECUTIVE), params)
            self.assertEqual(response.status_code, 200, label)
            self.assertIsNone(response.context["query"], label)
            self.assertFalse(response.context["analytics_form"].is_valid(), label)
            self.assertContains(response, message, msg_prefix=label)
            self.assertContains(response, "Correct the filters above", msg_prefix=label)
            self.assertNotContains(response, 'class="tvg-card"', msg_prefix=label)
            self.assertNotContains(
                response, "Create validated snapshot", msg_prefix=label
            )
            self.assertEqual(response.context["snapshot_fields"], {}, label)

    def test_valid_venture_filter_is_carried_into_snapshot_fields(self):
        venture = make_venture(company=self.company)
        self.login(self.analyst)
        params = {
            **self.dates(
                self.today - timedelta(days=20), self.today - timedelta(days=1)
            ),
            "venture": venture.pk,
            "source": "linkedin",
        }
        response = self.client.get(reverse(RECRUITMENT), params)
        self.assertEqual(response.status_code, 200)
        query = response.context["query"]
        self.assertEqual((query.venture_id, query.source), (venture.pk, "linkedin"))
        fields = response.context["snapshot_fields"]
        self.assertEqual(fields["venture"], venture.pk)
        self.assertEqual(fields["source"], "linkedin")

    def test_generate_button_requires_generate_permission(self):
        self.login(self.viewer)
        response = self.client.get(reverse(EXECUTIVE))
        self.assertFalse(response.context["can_generate"])
        self.assertNotContains(response, "Create validated snapshot")
        self.assertNotContains(response, reverse(CREATE))

        self.login(self.analyst)
        response = self.client.get(reverse(EXECUTIVE))
        self.assertTrue(response.context["can_generate"])
        self.assertContains(response, "Create validated snapshot")
        self.assertContains(response, reverse(CREATE))

    def test_export_button_requires_export_permission(self):
        snapshot = self.snapshot()
        detail = reverse("analytics:snapshot-detail", args=[snapshot.public_id])
        generator = make_actor(
            self.company, "synthetic.view.generator", "view", "generate"
        )
        for actor in (self.viewer, generator):
            self.login(actor)
            response = self.client.get(detail)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Publish to the Metabase reporting view")
        self.login(self.analyst)
        self.assertContains(
            self.client.get(detail), "Publish to the Metabase reporting view"
        )

    def test_horilla_report_links_follow_report_access(self):
        self.login(self.analyst)
        response = self.client.get(reverse(EXECUTIVE))
        links = {link["url"] for link in response.context["horilla_reports"]}
        for slug in REPORT_SLUGS.values():
            url = reverse("standard-report-detail", args=[slug])
            self.assertIn(url, links)
            self.assertContains(response, url)
        self.assertContains(response, "PeopleOps executive summary (PeopleOps report)")
        funnel = reverse("standard-report-detail", args=["recruitment-funnel"])
        self.assertNotIn(funnel, links)

        ReportAccess.objects.create(
            report_slug=REPORT_SLUGS["executive"],
            can_view=False,
            company_id=self.company,
        )
        response = self.client.get(reverse(EXECUTIVE))
        links = {link["url"] for link in response.context["horilla_reports"]}
        self.assertNotIn(
            reverse("standard-report-detail", args=[REPORT_SLUGS["executive"]]), links
        )
        self.assertIn(
            reverse("standard-report-detail", args=[REPORT_SLUGS["recruitment"]]), links
        )

        funnel_user = self.analyst.employee_user_id
        funnel_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="recruitment", codename="view_recruitment"
            )
        )
        response = self.client.get(reverse(EXECUTIVE))
        links = {link["url"] for link in response.context["horilla_reports"]}
        self.assertIn(funnel, links)


class SnapshotViewTests(AnalyticsViewTestCase):
    def test_get_on_snapshot_create_is_not_allowed(self):
        self.login(self.analyst)
        self.assertEqual(self.client.get(reverse(CREATE)).status_code, 405)

    def test_snapshot_create_builds_and_redirects_with_the_filters(self):
        venture = make_venture(company=self.company)
        self.login(self.analyst)
        params = {
            **self.dates(
                self.today - timedelta(days=20), self.today - timedelta(days=1)
            ),
            "venture": venture.pk,
        }
        response = self.client.get(reverse(EXECUTIVE), params)
        fields = response.context["snapshot_fields"]
        response = self.client.post(
            reverse(CREATE), {**fields, "dashboard": "recruitment"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], f"{reverse(RECRUITMENT)}?{urlencode(fields)}"
        )
        self.assertIn(f"venture={venture.pk}", response["Location"])
        snapshot = AnalyticsSnapshot.all_objects.get()
        self.assertEqual(snapshot.status, Status.VALIDATED, snapshot.last_error)
        self.assertEqual(snapshot.company, self.company)
        self.assertEqual(snapshot.generated_by, self.analyst)
        self.assertEqual(snapshot.filters["venture_id"], venture.pk)
        self.assertEqual(snapshot.period_start.isoformat(), fields["period_start"])
        self.assertTrue(any("revision 1" in m for m in self.messages(response)))

        page = self.client.get(response["Location"])
        self.assertEqual(page.context["current_snapshot"].pk, snapshot.pk)
        self.assertContains(page, "Rebuild snapshot with current data")

        rebuilt = self.client.post(
            reverse(CREATE), {**fields, "dashboard": "executive", "rebuild": "1"}
        )
        self.assertEqual(
            rebuilt["Location"], f"{reverse(EXECUTIVE)}?{urlencode(fields)}"
        )
        revisions = AnalyticsSnapshot.all_objects.order_by("revision")
        self.assertEqual(
            [(s.revision, s.status) for s in revisions],
            [(1, Status.SUPERSEDED), (2, Status.VALIDATED)],
        )

    def test_snapshot_create_with_failed_validation_reports_the_error(self):
        self.login(self.analyst)
        fields = self.client.get(reverse(EXECUTIVE)).context["snapshot_fields"]
        with mock.patch(COLLECT, side_effect=collecting(INCONSISTENT)):
            response = self.client.post(reverse(CREATE), fields)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AnalyticsSnapshot.all_objects.get().status, Status.FAILED)
        self.assertTrue(
            any(
                "applications_by_source_reconciles" in m
                for m in self.messages(response)
            )
        )

    def test_snapshot_create_requires_generate_permission(self):
        self.login(self.viewer)
        fields = self.client.get(reverse(EXECUTIVE)).context["snapshot_fields"]
        response = self.client.post(reverse(CREATE), fields)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_invalid_snapshot_create_is_a_bad_request(self):
        self.login(self.analyst)
        foreign_venture = make_venture(company=self.other)
        for payload in (
            {},
            self.dates(self.today - timedelta(days=1), self.today - timedelta(days=9)),
            {
                **self.dates(self.today - timedelta(days=9), self.today),
                "venture": foreign_venture.pk,
            },
        ):
            response = self.client.post(reverse(CREATE), payload)
            self.assertEqual(response.status_code, 400, payload)
        self.assertFalse(AnalyticsSnapshot.all_objects.exists())

    def test_snapshot_detail_shows_validation_checks_and_points(self):
        snapshot = self.snapshot()
        self.login(self.viewer)
        response = self.client.get(
            reverse("analytics:snapshot-detail", args=[snapshot.public_id])
        )
        self.assertEqual(response.status_code, 200)
        for code in (
            "point_count_matches_build",
            "applications_by_source_reconciles",
            "no_prohibited_tokens",
        ):
            self.assertContains(response, code)
        self.assertContains(response, "Passed")
        self.assertContains(response, "Unique applicants")
        self.assertEqual(response.context["snapshot"].pk, snapshot.pk)

    def test_another_companys_snapshot_is_not_found(self):
        foreign = self.snapshot(actor=self.foreign_analyst)
        self.login(self.analyst)
        detail = reverse("analytics:snapshot-detail", args=[foreign.public_id])
        export = reverse("analytics:snapshot-export", args=[foreign.public_id])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.post(export).status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Status.VALIDATED)
        self.assertFalse(AnalyticsExportAttempt.all_objects.exists())

    def test_exporting_a_non_validated_snapshot_redirects_with_an_error(self):
        with self.assertLogs("tvg_analytics.services", level="ERROR"):
            failed = self.snapshot(collect=mock.Mock(side_effect=RuntimeError("x")))
        self.assertEqual(failed.status, Status.FAILED)
        self.login(self.analyst)
        response = self.client.post(
            reverse("analytics:snapshot-export", args=[failed.public_id])
        )
        self.assertRedirects(
            response,
            reverse("analytics:snapshot-detail", args=[failed.public_id]),
            fetch_redirect_response=False,
        )
        self.assertIn(
            "Only validated analytics snapshots may be exported.",
            self.messages(response),
        )
        self.assertFalse(AnalyticsExportAttempt.all_objects.exists())

    def test_export_requires_export_permission(self):
        snapshot = self.snapshot()
        generator = make_actor(
            self.company, "synthetic.view.generator2", "view", "generate"
        )
        self.login(generator)
        response = self.client.post(
            reverse("analytics:snapshot-export", args=[snapshot.public_id])
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AnalyticsExportAttempt.all_objects.exists())

    def test_export_publishes_a_validated_snapshot(self):
        snapshot = self.snapshot()
        self.login(self.analyst)
        url = reverse("analytics:snapshot-export", args=[snapshot.public_id])
        with self.settings(TVG_ANALYTICS_EXPORT_PROVIDER="disabled"):
            response = self.client.post(url)
        self.assertTrue(
            any("analytics_export_disabled" in m for m in self.messages(response))
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, Status.VALIDATED)

        response = self.client.post(url)
        self.assertIn(
            "Snapshot published to the reporting view.", self.messages(response)
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, Status.EXPORTED)
        detail = self.client.get(
            reverse("analytics:snapshot-detail", args=[snapshot.public_id])
        )
        self.assertContains(detail, "Export attempts")
        self.assertNotContains(detail, "Publish to the Metabase reporting view")
        self.assertEqual(
            AnalyticsExportAttempt.all_objects.filter(snapshot=snapshot).count(), 2
        )
