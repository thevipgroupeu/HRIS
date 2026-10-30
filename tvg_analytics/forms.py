from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from base.models import Department, JobPosition
from tvg_careers.attribution import APPLICATION_SOURCES
from tvg_core.models import Venture

from .metrics import AnalyticsQuery

DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 366


class AnalyticsFilterForm(forms.Form):
    period_start = forms.DateField(
        label="Period start", widget=forms.DateInput(attrs={"type": "date"})
    )
    period_end = forms.DateField(
        label="Period end", widget=forms.DateInput(attrs={"type": "date"})
    )
    venture = forms.ModelChoiceField(
        queryset=Venture.all_objects.none(), required=False, empty_label="All ventures"
    )
    department = forms.ModelChoiceField(
        queryset=Department._base_manager.none(),
        required=False,
        empty_label="All departments",
    )
    role = forms.ModelChoiceField(
        queryset=JobPosition._base_manager.none(),
        required=False,
        empty_label="All roles",
    )
    source = forms.ChoiceField(
        label="Recruitment source",
        choices=(("", "All sources"), *APPLICATION_SOURCES),
        required=False,
    )

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["venture"].queryset = Venture.all_objects.filter(
            legal_employer=company, is_active=True
        ).order_by("name")
        self.fields["department"].queryset = (
            Department._base_manager.filter(company_id=company)
            .distinct()
            .order_by("department")
        )
        self.fields["role"].queryset = (
            JobPosition._base_manager.filter(company_id=company)
            .distinct()
            .order_by("job_position")
        )

    @staticmethod
    def default_data():
        today = timezone.localdate()
        return {
            "period_start": (
                today - timedelta(days=DEFAULT_PERIOD_DAYS - 1)
            ).isoformat(),
            "period_end": today.isoformat(),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("period_start")
        end = cleaned.get("period_end")
        if start and end and end < start:
            raise ValidationError("Reporting period end cannot precede its start.")
        if end and end > timezone.localdate():
            raise ValidationError(
                "Historical analytics periods cannot end in the future."
            )
        if start and end and (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise ValidationError(
                f"Reporting periods are limited to {MAX_PERIOD_DAYS} days."
            )
        return cleaned

    def to_query(self, company_id) -> AnalyticsQuery:
        data = self.cleaned_data
        return AnalyticsQuery(
            company_id=company_id,
            period_start=data["period_start"],
            period_end=data["period_end"],
            as_of_date=data["period_end"],
            venture_id=data["venture"].pk if data.get("venture") else None,
            department_id=data["department"].pk if data.get("department") else None,
            role_id=data["role"].pk if data.get("role") else None,
            source=data.get("source") or "",
        )


def query_params(query: AnalyticsQuery) -> dict:
    """Form field values for a query, for hidden inputs and redirects."""

    return {
        key: value
        for key, value in {
            "period_start": query.period_start.isoformat(),
            "period_end": query.period_end.isoformat(),
            "venture": query.venture_id,
            "department": query.department_id,
            "role": query.role_id,
            "source": query.source,
        }.items()
        if value not in (None, "")
    }


def query_string(query: AnalyticsQuery) -> str:
    return urlencode(query_params(query))
