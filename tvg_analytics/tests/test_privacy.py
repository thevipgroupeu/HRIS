"""Privacy guarantees of PeopleOps analytics.

Analytics must never read protected characteristics, never expose identity
fields as dimensions, filters or reporting columns, and never let a person's
name, e-mail address or pseudonymous id leave as a dimension value.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path

from django.test import TestCase, override_settings

from employee.models import Employee
from recruitment.models import Candidate
from report.registry import get_report
from tvg_analytics import metrics
from tvg_analytics.definitions import (
    DIMENSION_KEYS,
    FILTER_KEYS,
    METRICS,
    PROHIBITED_EXPORT_TOKENS,
    PROTECTED_CHARACTERISTIC_TOKENS,
    looks_like_identifier,
    prohibited_tokens,
    tokens,
)
from tvg_analytics.forms import AnalyticsFilterForm
from tvg_analytics.metrics import collect_metrics
from tvg_analytics.reports import REPORT_SLUGS
from tvg_analytics.services import build_batch, build_snapshot
from tvg_analytics.tests.test_snapshots import aware, make_actor, make_query, submit
from tvg_careers.models import Application
from tvg_careers.tests.factories import make_job_opening
from tvg_core.models import Person
from tvg_core.tests.factories import make_person, make_venture
from tvg_erasmus.models import ScreeningDecision, ScreeningEvent, VerificationCase

REPORTING = import_module(
    "tvg_analytics.migrations.0002_snapshot_revisions_reporting_view"
)


def _docstring_nodes(tree):
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.add(id(body[0].value))
    return nodes


def protected_references(source: str):
    """Identifiers and literals in ``source`` naming a protected characteristic."""

    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found = []

    def check(kind, text, line):
        hits = tokens(text) & PROTECTED_CHARACTERISTIC_TOKENS
        if hits:
            found.append((kind, line, text, sorted(hits)))

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                check("literal", node.value, line)
        elif isinstance(node, ast.keyword) and node.arg:
            check("keyword", node.arg, line)
        elif isinstance(node, ast.Attribute):
            check("attribute", node.attr, line)
        elif isinstance(node, ast.Name):
            check("name", node.id, line)
        elif isinstance(node, ast.alias):
            check("import", node.name, line)
    return found


class StaticPrivacyTests(TestCase):
    def test_metric_queries_never_name_a_protected_characteristic(self):
        source = Path(metrics.__file__).read_text(encoding="utf-8")
        self.assertEqual(protected_references(source), [])

    def test_the_scanner_detects_protected_lookups(self):
        sample = (
            "qs.filter(candidate__gender='female')\n"
            "qs.values('person__employee__dob')\n"
            "row.marital_status\n"
            "x = f'{a}__country'\n"
        )
        kinds = {kind for kind, *_rest in protected_references(sample)}
        self.assertEqual(kinds, {"keyword", "literal", "attribute"})
        self.assertEqual(protected_references('"""Gender is never read."""'), [])

    def test_protected_tokens_are_also_prohibited_from_export(self):
        self.assertLessEqual(PROTECTED_CHARACTERISTIC_TOKENS, PROHIBITED_EXPORT_TOKENS)

    def test_dimension_filter_and_reporting_column_names_are_not_identifiers(self):
        names = set(DIMENSION_KEYS) | set(FILTER_KEYS)
        names |= set(REPORTING.DIMENSION_COLUMNS) | set(REPORTING.FILTER_COLUMNS)
        aliases = set()
        for vendor in ("sqlite", "postgresql"):
            aliases |= set(
                re.findall(r"\bAS ([a-z_][a-z0-9_]*)", REPORTING._select(vendor))
            )
        self.assertIn("dim_source", aliases)
        self.assertIn("filter_venture_id", aliases)
        self.assertIn("suppressed", aliases)
        names |= aliases
        names |= {f"dim_{key}" for key in REPORTING.DIMENSION_COLUMNS}
        exposed = {
            name: prohibited_tokens(name) for name in names if prohibited_tokens(name)
        }
        self.assertEqual(exposed, {})

    def test_token_matching_is_word_based(self):
        self.assertEqual(prohibited_tokens("dim_candidate_name"), {"candidate", "name"})
        self.assertEqual(prohibited_tokens("filter_nationality"), {"nationality"})
        self.assertEqual(prohibited_tokens("documents_by_status"), set())
        self.assertEqual(prohibited_tokens("stage"), set())
        self.assertTrue(looks_like_identifier("synthetic.person@example.invalid"))
        self.assertTrue(looks_like_identifier("5f0c6c1e-8a43-4bde-9b4e-8f27b1f0e0aa"))
        self.assertFalse(looks_like_identifier("Synthetic Venture"))

    def test_metric_dimensions_are_aggregate_vocabulary(self):
        for key, definition in METRICS.items():
            for dimension in definition.dimensions:
                self.assertEqual(prohibited_tokens(dimension), set(), (key, dimension))


@override_settings(TIME_ZONE="Europe/Warsaw")
class FormPrivacyTests(TestCase):
    def test_filters_offer_no_protected_characteristic(self):
        company = make_company_for_forms()
        form = AnalyticsFilterForm(company=company)
        for name, field in form.fields.items():
            self.assertEqual(
                tokens(name) & PROTECTED_CHARACTERISTIC_TOKENS, set(), name
            )
            self.assertEqual(
                tokens(str(field.label or "")) & PROTECTED_CHARACTERISTIC_TOKENS,
                set(),
                name,
            )
        for forbidden in ("gender", "nationality", "country", "dob", "age"):
            self.assertNotIn(forbidden, form.fields)
        self.assertEqual(
            set(form.fields),
            {"period_start", "period_end", "venture", "department", "role", "source"},
        )

    def test_horilla_reports_offer_only_organisational_filters(self):
        for slug in REPORT_SLUGS.values():
            definition = get_report(slug)
            self.assertEqual(
                set(definition.filter_fields), {"department_id", "job_position_id"}
            )
            self.assertIsNone(definition.drilldown_fn)
            self.assertIsNone(definition.export_model)


def make_company_for_forms():
    from horilla.testkit import make_company

    return make_company("Synthetic Form Employer")


@override_settings(TIME_ZONE="Europe/Warsaw")
class DataPrivacyTests(TestCase):
    """Behavioural checks on a small synthetic recruitment fixture."""

    def setUp(self):
        from horilla.testkit import make_company

        self.company = make_company("Synthetic Privacy Employer")
        self.staff = make_actor(
            self.company, "synthetic.privacy.analyst", "view", "generate", "export"
        )
        self.query = make_query(self.company)
        start = self.query.period_start
        self.venture = make_venture(company=self.company, name="Synthetic Brand")
        first = make_job_opening(company=self.company, venture=self.venture)
        second = make_job_opening(company=self.company, venture=self.venture)
        self.alpha = make_person(
            company=self.company,
            email="synthetic.alpha@example.invalid",
            preferred_name="Synthetic Applicant Alpha",
        )
        self.beta = make_person(
            company=self.company,
            email="synthetic.beta@example.invalid",
            preferred_name="Synthetic Applicant Beta",
        )
        self.applications = [
            submit(
                first,
                person=self.alpha,
                source="linkedin",
                submitted_at=aware(start + timedelta(days=2)),
                name="Synthetic Candidate Alpha",
            ),
            submit(
                second,
                person=self.alpha,
                source="company_website",
                submitted_at=aware(start + timedelta(days=3)),
                name="Synthetic Candidate Alpha Again",
            ),
            submit(
                first,
                person=self.beta,
                source="erasmusintern",
                submitted_at=aware(start + timedelta(days=5)),
                name="Synthetic Candidate Beta",
            ),
        ]
        screening = ScreeningDecision.all_objects.create(
            application=self.applications[0],
            status=ScreeningDecision.Status.SHORTLISTED,
            decided_by=self.staff,
            decided_at=aware(start + timedelta(days=4)),
        )
        ScreeningEvent.all_objects.create(
            screening=screening,
            from_status=ScreeningDecision.Status.INITIAL_SCREENING,
            to_status=ScreeningDecision.Status.SHORTLISTED,
            actor=self.staff,
            occurred_at=aware(start + timedelta(days=4)),
        )
        VerificationCase.all_objects.create(
            application=self.applications[0],
            status=VerificationCase.Status.REQUESTED,
            requested_at=aware(start + timedelta(days=6)),
            requested_by=self.staff,
        )

    def identifying_strings(self):
        values = set()
        for person in Person.all_objects.all():
            values |= {
                person.primary_email,
                person.preferred_name,
                str(person.public_id),
                person.public_id.hex,
            }
        for application in Application.all_objects.all():
            values |= {str(application.public_id), application.public_id.hex}
        candidate_ids = Application.all_objects.values_list("candidate_id", flat=True)
        for name, email in Candidate._base_manager.filter(
            pk__in=candidate_ids
        ).values_list("name", "email"):
            values |= {name, email}
        for first, last, email in Employee._base_manager.values_list(
            "employee_first_name", "employee_last_name", "email"
        ):
            values |= {email, f"{first} {last}"}
        return {value for value in values if value}

    def test_fixture_produces_non_trivial_metrics(self):
        points = {
            p["metric_key"]: p["value"]
            for p in collect_metrics(self.query)
            if not p["dimensions"]
        }
        self.assertEqual(points["applications_total"], 3)
        self.assertEqual(points["unique_persons"], 2)
        self.assertEqual(points["applications_screened"], 1)
        self.assertEqual(points["verification_requested"], 1)

    def test_metrics_ignore_protected_characteristics(self):
        before = collect_metrics(self.query)
        candidate_ids = list(
            Application.all_objects.values_list("candidate_id", flat=True)
        )
        variants = (
            ("female", date(1990, 1, 1), "Synthetica"),
            ("other", date(2001, 6, 30), "Testland"),
            ("male", date(1979, 12, 31), "Fictonia"),
        )
        for candidate_id, (gender, dob, country) in zip(candidate_ids, variants):
            Candidate._base_manager.filter(pk=candidate_id).update(
                gender=gender, dob=dob, country=country
            )
        Employee._base_manager.update(
            gender="other",
            dob=date(1985, 5, 5),
            marital_status="married",
            country="Synthetica",
        )
        Application.all_objects.update(
            questionnaire_snapshot=[
                {"question": "Nationality", "answer": "Synthetic nationality"},
                {"question": "Health conditions", "answer": "Synthetic health answer"},
                {"question": "Disability", "answer": "Synthetic answer"},
            ]
        )
        after = collect_metrics(self.query)
        self.assertEqual(after, before)

    def test_dimension_values_never_identify_a_person(self):
        forbidden = self.identifying_strings()
        self.assertIn("synthetic.alpha@example.invalid", forbidden)
        self.assertIn("Synthetic Candidate Beta", forbidden)
        points = collect_metrics(self.query)
        values = [
            str(value)
            for point in points
            for value in point["dimensions"].values()
            if value is not None
        ]
        self.assertTrue(values)
        for value in values:
            self.assertNotIn(value, forbidden)
            self.assertFalse(looks_like_identifier(value), value)
            for secret in forbidden:
                self.assertNotIn(secret, value)

    def test_snapshot_payload_never_identifies_a_person(self):
        snapshot = build_snapshot(query=self.query, actor=self.staff)
        self.assertEqual(snapshot.status, "validated", snapshot.last_error)
        payload = json.dumps(build_batch(snapshot).to_payload())
        for secret in self.identifying_strings():
            self.assertNotIn(secret, payload)
        # Every application-level breakdown cell here is below the threshold.
        decoded = json.loads(payload)
        by_source = [
            m for m in decoded["metrics"] if m["metric_key"] == "applications_by_source"
        ]
        self.assertTrue(by_source)
        for metric in by_source:
            self.assertTrue(metric["suppressed"])
            self.assertIsNone(metric["value"])
