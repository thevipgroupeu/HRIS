from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from employee.models import Employee
from tvg_interviews.models import (
    COMPANY_TIME_ZONE,
    DecisionNotificationTemplate,
    InterviewFeedback,
    InterviewSlot,
    InterviewType,
    validate_iana_timezone,
)


class InterviewTypeAdminForm(forms.ModelForm):
    class Meta:
        model = InterviewType
        fields = "__all__"

    def clean_default_interviewers(self):
        interviewers = self.cleaned_data["default_interviewers"]
        recruitment = self.cleaned_data.get("recruitment")
        if (
            recruitment
            and interviewers.exclude(
                employee_work_info__company_id=recruitment.company_id
            ).exists()
        ):
            raise ValidationError("Interviewers must belong to the legal employer.")
        return interviewers


class DecisionTemplateAdminForm(forms.ModelForm):
    class Meta:
        model = DecisionNotificationTemplate
        fields = "__all__"


class InvitationForm(forms.Form):
    interview_type = forms.ModelChoiceField(queryset=InterviewType.all_objects.none())
    interviewers = forms.ModelMultipleChoiceField(
        queryset=Employee._base_manager.none()
    )
    candidate_timezone = forms.CharField(
        max_length=64,
        initial=COMPANY_TIME_ZONE,
        help_text="IANA timezone, for example Europe/Paris.",
    )
    slot_starts = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=(
            "One ISO 8601 time per line with an explicit UTC offset, for example "
            "2026-11-03T10:00:00+01:00."
        ),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        self.application = application
        self.fields["interview_type"].queryset = InterviewType.all_objects.filter(
            recruitment=application.opening.recruitment,
            is_active=True,
        )
        self.fields["interviewers"].queryset = Employee._base_manager.filter(
            employee_work_info__company_id=application.opening.venture.legal_employer,
            is_active=True,
        )

    def clean_candidate_timezone(self):
        value = self.cleaned_data["candidate_timezone"].strip()
        validate_iana_timezone(value)
        return value

    def clean_slot_starts(self):
        rows = [
            row.strip()
            for row in self.cleaned_data["slot_starts"].splitlines()
            if row.strip()
        ]
        if not rows or len(rows) > 20:
            raise ValidationError("Provide between 1 and 20 slots.")
        values = []
        for row in rows:
            value = parse_datetime(row)
            if not value or timezone.is_naive(value):
                raise ValidationError(
                    "Every slot must be an ISO 8601 datetime with a UTC offset."
                )
            values.append(value)
        return values


class FeedbackForm(forms.Form):
    competency_ratings = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text='JSON object using ratings 1-5, for example {"Communication": 4}.',
    )
    strengths = forms.CharField(required=False, widget=forms.Textarea)
    concerns = forms.CharField(required=False, widget=forms.Textarea)
    private_notes = forms.CharField(required=False, widget=forms.Textarea)
    recommendation = forms.ChoiceField(choices=InterviewFeedback.Recommendation.choices)

    def clean_competency_ratings(self):
        try:
            value = json.loads(self.cleaned_data["competency_ratings"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("Enter a valid JSON object.") from exc
        if not isinstance(value, dict):
            raise ValidationError("Enter a JSON object.")
        return value


class DecisionForm(forms.Form):
    outcome = forms.ChoiceField(choices=DecisionNotificationTemplate.Outcome.choices)
    template = forms.ModelChoiceField(
        queryset=DecisionNotificationTemplate.all_objects.none(),
        required=False,
    )
    internal_reason = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, recruitment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = (
            DecisionNotificationTemplate.all_objects.filter(
                recruitment=recruitment,
                approved=True,
                is_active=True,
            )
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get(
            "outcome"
        ) == DecisionNotificationTemplate.Outcome.REJECTED and not cleaned.get(
            "template"
        ):
            self.add_error("template", "Rejection requires an approved template.")
        return cleaned


class StaffRescheduleForm(forms.Form):
    slot = forms.ModelChoiceField(queryset=InterviewSlot.all_objects.none())
    override_notice = forms.BooleanField(required=False)

    def __init__(self, *args, invitation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slot"].queryset = InterviewSlot.all_objects.filter(
            invitation=invitation,
            status=InterviewSlot.Status.AVAILABLE,
            start_at__gt=timezone.now(),
        )
