from __future__ import annotations

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from .file_security import inspect_presentation
from .models import ReportingRubricVersion, ReviewOutcome
from .services import available_projects, available_tasks, company_id_for


class WeeklyReportForm(forms.Form):
    reporting_week = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Choose the Monday that starts the reporting week.",
    )
    assigned_project = forms.ModelChoiceField(queryset=None)
    planned_objectives = forms.CharField(widget=forms.Textarea)
    completed_tasks = forms.CharField(widget=forms.Textarea)
    completed_task_records = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Native project tasks included as completion evidence.",
    )
    evidence_links = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One HTTPS link per line. Do not include credentials or personal data.",
    )
    blockers = forms.CharField(required=False, widget=forms.Textarea)
    learning_outcomes = forms.CharField(widget=forms.Textarea)
    next_week_plan = forms.CharField(widget=forms.Textarea)
    hours = forms.DecimalField(
        required=False, min_value=0, max_digits=6, decimal_places=2
    )
    progress_percent = forms.IntegerField(required=False, min_value=0, max_value=100)

    def __init__(self, *args, employee, report=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.report = report
        self.fields["assigned_project"].queryset = available_projects(employee)
        project = None
        if self.is_bound:
            try:
                project = self.fields["assigned_project"].queryset.get(
                    pk=self.data.get("assigned_project")
                )
            except (
                ValueError,
                TypeError,
                self.fields["assigned_project"].queryset.model.DoesNotExist,
            ):
                pass
        elif report:
            project = report.assigned_project
            self.initial.update(
                {
                    "reporting_week": report.reporting_week,
                    "assigned_project": report.assigned_project_id,
                    "planned_objectives": report.planned_objectives,
                    "completed_tasks": report.completed_tasks,
                    "completed_task_records": report.completed_task_records.all(),
                    "evidence_links": "\n".join(report.evidence_links),
                    "blockers": report.blockers,
                    "learning_outcomes": report.learning_outcomes,
                    "next_week_plan": report.next_week_plan,
                    "hours": report.hours,
                    "progress_percent": report.progress_percent,
                }
            )
        self.fields["completed_task_records"].queryset = (
            available_tasks(employee, project)
            if project
            else available_tasks(employee, None)
        )

    def clean_evidence_links(self):
        return [
            value.strip()
            for value in self.cleaned_data["evidence_links"].splitlines()
            if value.strip()
        ]


class MonthlyPresentationForm(forms.Form):
    presentation_month = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Use the first day of the presentation month.",
    )
    assigned_project = forms.ModelChoiceField(queryset=None)
    presentation_file = forms.FileField(
        required=False,
        help_text="PDF or PPTX, maximum 20 MB. Active content is rejected.",
    )
    presentation_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    project_milestones = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, employee, presentation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.presentation = presentation
        self.fields["assigned_project"].queryset = available_projects(employee)
        if presentation:
            self.initial.update(
                {
                    "presentation_month": presentation.presentation_month,
                    "assigned_project": presentation.assigned_project_id,
                    "presentation_date": presentation.presentation_date,
                    "project_milestones": presentation.project_milestones,
                }
            )

    def clean_presentation_file(self):
        upload = self.cleaned_data.get("presentation_file")
        if not upload:
            return None
        inspected = inspect_presentation(upload)
        return {
            "file": SimpleUploadedFile(
                inspected.original_filename,
                inspected.content,
                content_type=inspected.content_type,
            ),
            "original_filename": inspected.original_filename,
            "content_type": inspected.content_type,
            "sha256": inspected.sha256,
            "size_bytes": len(inspected.content),
        }


class ReviewForm(forms.Form):
    outcome = forms.ChoiceField(choices=ReviewOutcome.choices)
    comments = forms.CharField(widget=forms.Textarea)
    evidence_review = forms.CharField(required=False, widget=forms.Textarea)
    development_feedback = forms.CharField(required=False, widget=forms.Textarea)
    writing_quality_comments = forms.CharField(
        required=False,
        widget=forms.Textarea,
        help_text="Assess report/presentation writing separately from internship performance.",
    )
    internship_performance_comments = forms.CharField(
        required=False,
        widget=forms.Textarea,
        help_text="Human supervisor assessment only.",
    )
    rubric = forms.ModelChoiceField(queryset=None)
    rubric_scores = forms.JSONField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text='JSON object keyed by rubric criterion, for example {"evidence": 3}.',
    )
    reviewer_adjustments = forms.JSONField(
        required=False,
        initial=dict,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional auditable adjustments to computed rubric data.",
    )
    adjustment_reason = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, reviewer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rubric"].queryset = ReportingRubricVersion.all_objects.filter(
            company_id=company_id_for(reviewer), approved_at__isnull=False
        ).order_by("name", "-version_number")
