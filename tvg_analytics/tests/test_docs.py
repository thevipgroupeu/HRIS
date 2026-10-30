"""Keep the published KPI and event contract synchronized with code."""

import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

from tvg_analytics.definitions import EVENT_MAPPINGS, METRICS, SCHEMA_VERSION

DOC = Path(__file__).resolve().parents[2] / "docs" / "analytics" / "PEOPLEOPS_KPIS.md"


def _section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def _table_rows(section):
    rows = {}
    pattern = re.compile(
        r"^\| `([^`]+)` \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|",
        re.MULTILINE,
    )
    for key, label, unit, semantics in pattern.findall(section):
        rows[key] = {
            "label": label.strip(),
            "unit": unit.strip(),
            "semantics": semantics.strip(),
        }
    return rows


class AnalyticsContractDocumentationTests(SimpleTestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_schema_version_and_every_metric_are_documented_exactly(self):
        self.assertIn(f"**KPI schema version**: {SCHEMA_VERSION}", self.text)
        catalogue = _section(self.text, "## KPI catalogue", "## Event mappings")
        rows = _table_rows(catalogue)
        self.assertEqual(set(rows), set(METRICS))
        for key, definition in METRICS.items():
            self.assertEqual(rows[key]["label"], str(definition.label), key)
            self.assertEqual(rows[key]["unit"], definition.unit, key)
            self.assertEqual(rows[key]["semantics"], definition.semantics, key)

    def test_every_event_is_documented_and_references_a_real_model_field(self):
        event_section = _section(
            self.text, "## Event mappings", "## Automated validation and reconciliation"
        )
        documented = set(
            re.findall(r"^\| `([^`]+\.[^`]+)` \|", event_section, re.MULTILINE)
        )
        self.assertEqual(documented, set(EVENT_MAPPINGS))
        for key, mapping in EVENT_MAPPINGS.items():
            app_label, model_name = mapping.model.split(".", 1)
            model = apps.get_model(app_label, model_name)
            self.assertIsNotNone(model, key)
            self.assertIsNotNone(model._meta.get_field(mapping.timestamp), key)
            self.assertIn(f"`{mapping.model}.{mapping.timestamp}`", event_section)
