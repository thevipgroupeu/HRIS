"""TVG analytics exposed through Horilla's standard report catalogue.

Registering through ``report.registry.register`` reuses Horilla's report
access matrix, exports and subscriptions without editing upstream files. The
reports are aggregate-only (no drill-down to people), and the company always
comes from Horilla's selected company, never from a request parameter.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from horilla.horilla_middlewares import get_selected_company

from .definitions import BREAKDOWN_KEYS, CARD_KEYS, DETAIL_KEYS, METRICS
from .metrics import AnalyticsQuery, collect_metrics
from .presentation import dimension_label

REPORT_SLUGS = {
    "executive": "tvg-peopleops-executive",
    "recruitment": "tvg-recruitment-pipeline",
}


def selected_company_id(filters=None):
    selected = get_selected_company()
    if selected in (None, "", "all"):
        return None
    try:
        selected = int(selected)
    except (TypeError, ValueError):
        return None
    requested = getattr(filters, "company_id", None)
    if requested and int(requested) != selected:
        return None
    return selected


def query_from_report_filters(filters):
    company_id = selected_company_id(filters)
    if not company_id:
        return None
    end = min(filters.to_date, timezone.localdate())
    start = min(filters.from_date, end)
    return AnalyticsQuery(
        company_id=company_id,
        period_start=start,
        period_end=end,
        as_of_date=end,
        department_id=filters.department_id,
        role_id=filters.job_position_id,
    )


def _number(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


def report_payload(dashboard, query):
    columns = [
        {"key": "metric", "label": _("Metric")},
        {"key": "dimension", "label": _("Breakdown")},
        {"key": "value", "label": _("Value")},
        {"key": "unit", "label": _("Unit")},
    ]
    if query is None:
        return {
            "kpis": [],
            "charts": [],
            "table": {"columns": columns, "rows": []},
            "notice": _("Select a single company to view PeopleOps analytics."),
        }
    points = collect_metrics(query)
    scalars = {p["metric_key"]: p for p in points if not p["dimensions"]}
    kpis = [
        {
            "label": METRICS[key].label,
            "value": _number(scalars[key]["value"]),
            "hint": METRICS[key].definition,
        }
        for key in CARD_KEYS[dashboard]
    ]
    charts = []
    rows = [
        {
            "metric": METRICS[key].label,
            "dimension": "",
            "value": _number(scalars[key]["value"]),
            "unit": METRICS[key].unit,
        }
        for key in (*CARD_KEYS[dashboard], *DETAIL_KEYS[dashboard])
    ]
    for key in BREAKDOWN_KEYS[dashboard]:
        series = [p for p in points if p["metric_key"] == key]
        if not series:
            continue
        peers = [p["dimensions"] for p in series]
        labels = [dimension_label(p["dimensions"], peers) for p in series]
        charts.append(
            {
                "id": key,
                "type": "bar",
                "title": METRICS[key].label,
                "categories": labels,
                "series": [
                    {
                        "name": METRICS[key].unit,
                        "data": [_number(p["value"]) for p in series],
                    }
                ],
            }
        )
        rows += [
            {
                "metric": METRICS[key].label,
                "dimension": label,
                "value": _number(p["value"]),
                "unit": METRICS[key].unit,
            }
            for label, p in zip(labels, series)
        ]
    return {"kpis": kpis, "charts": charts, "table": {"columns": columns, "rows": rows}}


def executive_report(filters):
    return report_payload("executive", query_from_report_filters(filters))


def recruitment_report(filters):
    return report_payload("recruitment", query_from_report_filters(filters))


def register_reports():
    from report.registry import ReportDefinition, register

    common = {
        "domain": "talent",
        "permission": "tvg_analytics.view_peopleops_analytics",
        "required_apps": ("tvg_careers", "tvg_analytics"),
        "filter_fields": ("department_id", "job_position_id"),
        "chart_hint": "bar",
    }
    register(
        ReportDefinition(
            slug=REPORT_SLUGS["executive"],
            name=_("PeopleOps executive summary"),
            description=_(
                "Aggregate CareerHub, internship and administration KPIs for the "
                "selected company. Counts distinguish people, applications, "
                "interviews, offers, acceptances and actual starts."
            ),
            query_fn=executive_report,
            **common,
        )
    )
    register(
        ReportDefinition(
            slug=REPORT_SLUGS["recruitment"],
            name=_("CareerHub recruitment pipeline"),
            description=_(
                "CareerHub applications by source, venture, department, role and "
                "month, screening outcomes, decisions, stage durations and open "
                "bottlenecks. Aggregate only."
            ),
            query_fn=recruitment_report,
            **common,
        )
    )
