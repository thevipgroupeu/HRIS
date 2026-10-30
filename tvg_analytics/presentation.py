"""Display helpers shared by the PeopleOps dashboards and Horilla reports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tvg_careers.attribution import APPLICATION_SOURCES

from .definitions import BREAKDOWN_KEYS, CARD_KEYS, DETAIL_KEYS, METRICS

SOURCE_LABELS = {code: str(label) for code, label in APPLICATION_SOURCES}
ENTITY_KEYS = ("venture", "department", "role")
UNIT_SUFFIX = {"percent": "%", "days": " days"}
SEMANTICS_LABELS = {
    "period": "events in the period",
    "cohort": "period cohort, outcome at the as-of date",
    "as_of": "state on the as-of date",
    "current_state": "current backlog at generation time",
}


def _humanize(value) -> str:
    return str(value).replace("_", " ").capitalize()


def dimension_label(dimensions: dict, peers=()) -> str:
    """Human label for one breakdown row.

    ``peers`` are the dimension dicts of the whole breakdown; entity names that
    are not unique within it (Horilla allows duplicate department names) are
    disambiguated with their id.
    """

    for key in ENTITY_KEYS:
        if key in dimensions:
            label = str(dimensions[key])
            same_name = sum(1 for other in peers if str(other.get(key)) == label)
            if same_name > 1 and dimensions.get(f"{key}_id") is not None:
                return f"{label} (#{dimensions[f'{key}_id']})"
            return label
    if "source" in dimensions:
        return SOURCE_LABELS.get(dimensions["source"], _humanize(dimensions["source"]))
    if "month" in dimensions:
        try:
            return date.fromisoformat(dimensions["month"]).strftime("%b %Y")
        except ValueError:
            return str(dimensions["month"])
    if "horizon_days" in dimensions:
        return f"Within {dimensions['horizon_days']} days"
    for key in ("screening_outcome", "status", "stage", "checklist_item"):
        if key in dimensions:
            return _humanize(dimensions[key])
    return ", ".join(f"{key}: {value}" for key, value in sorted(dimensions.items()))


def _field(point, name):
    return point[name] if isinstance(point, dict) else getattr(point, name)


def _metric(key, point=None):
    definition = METRICS[key]
    denominator = _field(point, "denominator") if point is not None else None
    return {
        "key": key,
        "label": definition.label,
        "definition": definition.definition,
        "unit": definition.unit,
        "suffix": UNIT_SUFFIX.get(definition.unit, ""),
        "semantics": definition.semantics,
        "semantics_label": SEMANTICS_LABELS[definition.semantics],
        "company_wide": definition.company_wide,
        "value": _field(point, "value") if point is not None else Decimal(0),
        "numerator": _field(point, "numerator") if point is not None else None,
        "denominator": denominator,
        "no_data": (definition.is_rate or definition.is_duration) and not denominator,
    }


def build_dashboard(dashboard: str, points) -> dict:
    """Group metric points (dicts or model rows) into cards and breakdowns."""

    scalars, series = {}, {}
    for point in points:
        key = _field(point, "metric_key")
        if key not in METRICS:
            continue
        if _field(point, "dimensions"):
            series.setdefault(key, []).append(point)
        else:
            scalars[key] = point
    breakdowns = []
    for key in BREAKDOWN_KEYS[dashboard]:
        rows = series.get(key, [])
        peers = [_field(point, "dimensions") for point in rows]
        largest = max((_field(point, "value") for point in rows), default=0) or 1
        breakdowns.append(
            {
                **_metric(key),
                "rows": [
                    {
                        "label": dimension_label(_field(point, "dimensions"), peers),
                        "value": _field(point, "value"),
                        "numerator": _field(point, "numerator"),
                        "denominator": _field(point, "denominator"),
                        "share": int(_field(point, "value") * 100 / largest),
                    }
                    for point in rows
                ],
            }
        )
    return {
        "cards": [_metric(key, scalars.get(key)) for key in CARD_KEYS[dashboard]],
        "details": [_metric(key, scalars.get(key)) for key in DETAIL_KEYS[dashboard]],
        "breakdowns": breakdowns,
    }
