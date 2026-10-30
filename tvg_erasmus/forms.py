from django import forms

from base.models import Department
from employee.models import Employee
from tvg_careers.document_security import inspect_candidate_document
from tvg_erasmus.models import (
    AcceptanceLetterTemplate,
    DurationException,
    ErasmusAccommodationRequest,
    ErasmusCase,
    ErasmusDocumentRequirement,
    ErasmusDocumentTemplate,
    ScreeningDecision,
    VerificationCase,
)


class ScreeningActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            (ScreeningDecision.Status.SHORTLISTED, "Shortlist"),
            (ScreeningDecision.Status.REJECTED, "Reject"),
            (ScreeningDecision.Status.HOLD, "Put on hold"),
            (ScreeningDecision.Status.INFORMATION_REQUESTED, "Request information"),
        ]
    )
    applicant_message = forms.CharField(max_length=500, required=False)
    internal_note = forms.CharField(widget=forms.Textarea, required=False)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("action") == ScreeningDecision.Status.INFORMATION_REQUESTED
            and not (cleaned.get("applicant_message") or "").strip()
        ):
            self.add_error(
                "applicant_message",
                "Explain securely what information the applicant should provide.",
            )
        return cleaned


class VerificationReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (VerificationCase.Status.UNDER_REVIEW, "Start review"),
            (VerificationCase.Status.VERIFIED, "Approve"),
            (VerificationCase.Status.REVISION_NEEDED, "Request correction"),
            (VerificationCase.Status.REJECTED, "Reject document"),
        ]
    )
    reason = forms.CharField(max_length=500, required=False)
    internal_note = forms.CharField(widget=forms.Textarea, required=False)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("decision")
            in {
                VerificationCase.Status.REVISION_NEEDED,
                VerificationCase.Status.REJECTED,
            }
            and not (cleaned.get("reason") or "").strip()
        ):
            self.add_error("reason", "A candidate-facing reason is required.")
        return cleaned


class ErasmusCaseForm(forms.ModelForm):
    class Meta:
        model = ErasmusCase
        fields = (
            "university",
            "programme",
            "funding_declaration",
            "proposed_start_date",
            "proposed_end_date",
            "working_model",
            "work_location",
            "department",
            "supervisor",
            "curriculum_reference",
            "curriculum_version",
            "university_confirmation_status",
        )
        widgets = {
            "proposed_start_date": forms.DateInput(attrs={"type": "date"}),
            "proposed_end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        employer = application.opening.venture.legal_employer
        self.fields["department"].queryset = Department.objects.filter(
            company_id=employer
        ).distinct()
        self.fields["supervisor"].queryset = Employee._base_manager.filter(
            employee_work_info__company_id=employer,
            is_active=True,
        )


class DurationExceptionRequestForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Explain why the confirmed internship is shorter than policy.",
    )


class DurationExceptionDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(
            (DurationException.Status.APPROVED, "Approve exception"),
            (DurationException.Status.REJECTED, "Reject exception"),
        )
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("decision") == DurationException.Status.REJECTED
            and not (cleaned.get("note") or "").strip()
        ):
            self.add_error("note", "A rejection note is required.")
        return cleaned


class AcceptanceLetterIssueForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=AcceptanceLetterTemplate.all_objects.none()
    )
    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = AcceptanceLetterTemplate.all_objects.filter(
            company=company,
            approved_at__isnull=False,
            approved_by__isnull=False,
            is_active=True,
        )


class AcceptanceTemplateAdminForm(forms.ModelForm):
    class Meta:
        model = AcceptanceLetterTemplate
        fields = "__all__"

    def clean_body(self):
        value = self.cleaned_data["body"]
        if "<script" in value.casefold() or "<iframe" in value.casefold():
            raise forms.ValidationError("Templates are plain text only.")
        return value


class ErasmusDocumentTemplateAdminForm(forms.ModelForm):
    document_type = forms.ChoiceField(
        choices=ErasmusDocumentRequirement.DocumentType.choices
    )

    class Meta:
        model = ErasmusDocumentTemplate
        exclude = (
            "content_type",
            "sha256",
            "size_bytes",
            "approved_by",
            "approved_at",
        )

    def clean_source_file(self):
        upload = self.cleaned_data["source_file"]
        inspected = inspect_candidate_document(
            upload,
            max_bytes=10 * 1024 * 1024,
            allowed_extensions=(".pdf", ".docx"),
        )
        self.inspected = inspected
        return upload

    def save(self, commit=True):
        instance = super().save(commit=False)
        inspected = self.inspected
        instance.content_type = inspected.content_type
        instance.sha256 = inspected.sha256
        instance.size_bytes = len(inspected.content)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ErasmusDocumentUploadForm(forms.Form):
    document = forms.FileField(help_text="PDF or DOCX, maximum 10 MB.")

    def clean_document(self):
        upload = self.cleaned_data["document"]
        inspect_candidate_document(
            upload,
            max_bytes=10 * 1024 * 1024,
            allowed_extensions=(".pdf", ".docx"),
        )
        return upload


class ErasmusDocumentReviewForm(forms.Form):
    action = forms.ChoiceField(
        choices=(("review", "Mark reviewed"), ("revision", "Request revision"))
    )
    revision_reason = forms.CharField(
        max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("action") == "revision"
            and not (cleaned.get("revision_reason") or "").strip()
        ):
            self.add_error("revision_reason", "A candidate-facing reason is required.")
        return cleaned


class ErasmusManualSignatureForm(ErasmusDocumentUploadForm):
    signer_roles = forms.MultipleChoiceField(
        choices=ErasmusDocumentRequirement.SignerRole.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, document, **kwargs):
        super().__init__(*args, **kwargs)
        required = set(document.required_signer_roles)
        self.fields["signer_roles"].choices = [
            choice
            for choice in ErasmusDocumentRequirement.SignerRole.choices
            if choice[0] in required
        ]


class ErasmusRecordSignatureForm(forms.Form):
    signer_role = forms.ChoiceField(
        choices=ErasmusDocumentRequirement.SignerRole.choices
    )

    def __init__(self, *args, document, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["signer_role"].choices = [
            choice
            for choice in ErasmusDocumentRequirement.SignerRole.choices
            if choice[0] in set(document.required_signer_roles)
        ]


class ErasmusAccommodationFollowUpForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            (ErasmusAccommodationRequest.Status.CONTACTED, "Contacted"),
            (ErasmusAccommodationRequest.Status.CLOSED, "Closed"),
        )
    )
