from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError

from asset.models import AssetAssignment
from tvg_reporting.models import (
    MonthlyPresentation,
    ReportingRubricVersion,
    WeeklyReport,
)

from .models import CompletionDocumentTemplate


class FinalPresentationScheduleForm(forms.Form):
    scheduled_for = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )


class FinalReportLinkForm(forms.Form):
    weekly_report = forms.ModelChoiceField(queryset=WeeklyReport.all_objects.none())
    monthly_presentation = forms.ModelChoiceField(
        queryset=MonthlyPresentation.all_objects.none()
    )

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekly_report"].queryset = WeeklyReport.all_objects.filter(
            employee=case.employee
        )
        self.fields["monthly_presentation"].queryset = (
            MonthlyPresentation.all_objects.filter(employee=case.employee)
        )


class FinalFeedbackForm(forms.Form):
    comments = forms.CharField(widget=forms.Textarea)
    task_completion_evidence = forms.CharField(required=False, widget=forms.Textarea)
    development_feedback = forms.CharField(required=False, widget=forms.Textarea)
    writing_quality_comments = forms.CharField(required=False, widget=forms.Textarea)
    internship_performance_comments = forms.CharField(
        required=False, widget=forms.Textarea
    )
    rubric = forms.ModelChoiceField(queryset=ReportingRubricVersion.all_objects.none())
    rubric_scores = forms.CharField(
        widget=forms.Textarea,
        help_text='JSON object keyed by criterion code, for example {"delivery": 4}.',
    )
    reviewer_adjustments = forms.CharField(
        required=False,
        widget=forms.Textarea,
        help_text="Optional JSON object; a reason is mandatory when adjustments are used.",
    )
    adjustment_reason = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        company = case.onboarding.venture.legal_employer
        self.fields["rubric"].queryset = ReportingRubricVersion.all_objects.filter(
            company=company, approved_at__isnull=False
        )

    def _json_object(self, field, *, default=None):
        value = self.cleaned_data.get(field, "").strip()
        if not value:
            return {} if default is None else default
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError("Enter a valid JSON object.") from exc
        if not isinstance(parsed, dict):
            raise ValidationError("Enter a JSON object.")
        return parsed

    def clean_rubric_scores(self):
        return self._json_object("rubric_scores")

    def clean_reviewer_adjustments(self):
        return self._json_object("reviewer_adjustments")


class AssetReturnForm(forms.Form):
    assignment = forms.ModelChoiceField(queryset=AssetAssignment._base_manager.none())
    return_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    return_status = forms.ChoiceField(choices=AssetAssignment.STATUS)
    return_condition = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignment"].queryset = AssetAssignment._base_manager.filter(
            assigned_to_employee_id=case.employee,
            return_date__isnull=True,
        )


class ManualRevocationForm(forms.Form):
    proof_reference = forms.CharField(
        required=False,
        max_length=180,
        help_text="Use a non-secret ticket or audit reference.",
    )
    proof_upload = forms.FileField(
        required=False, help_text="Optional PDF proof, maximum 10 MB."
    )


class DocumentPrepareForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=CompletionDocumentTemplate.all_objects.none()
    )
    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = (
            CompletionDocumentTemplate.all_objects.filter(
                company=case.onboarding.venture.legal_employer,
                approved_at__isnull=False,
            )
        )


class SignatureForm(forms.Form):
    signer_role = forms.ChoiceField(
        choices=CompletionDocumentTemplate.SignerRole.choices
    )
    signed_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    evidence_reference = forms.CharField(required=False, max_length=180)


class ExceptionForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)
