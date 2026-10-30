from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tvg_careers.accounts.models import (
    CandidateAccount,
    CandidateProfile,
    CandidateProfileDocument,
    normalize_candidate_email,
)
from tvg_careers.document_security import inspect_candidate_document
from tvg_careers.verification_document_security import inspect_verification_document


class CandidateSignupForm(forms.Form):
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    email = forms.EmailField(
        max_length=254,
        label="Personal email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    privacy_notice_acknowledged = forms.BooleanField(
        label="I have read the CareerHub privacy notice.",
    )
    talent_pool_consent = forms.BooleanField(
        required=False,
        label=(
            "I agree to be contacted about other relevant THEVIPGROUP "
            "opportunities. This is optional and can be withdrawn."
        ),
    )

    def clean_email(self):
        return normalize_candidate_email(self.cleaned_data["email"])

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match.")
        if password1:
            candidate = CandidateAccount(
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            try:
                password_validation.validate_password(password1, candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def create_account(self):
        now = timezone.now()
        return CandidateAccount.objects.create_user(
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            privacy_notice_version=settings.CAREERHUB_PRIVACY_NOTICE_VERSION,
            privacy_notice_acknowledged_at=now,
            talent_pool_consent=self.cleaned_data["talent_pool_consent"],
            talent_pool_consent_updated_at=now,
        )


class CandidateLoginForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        label="Personal email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def clean_email(self):
        return normalize_candidate_email(self.cleaned_data["email"])


class CandidateProfileForm(forms.ModelForm):
    phone_number = forms.CharField(max_length=15, required=False)
    address_line = forms.CharField(max_length=255, required=False)
    city = forms.CharField(max_length=80, required=False)
    postal_code = forms.CharField(max_length=24, required=False)
    country_of_residence = forms.CharField(max_length=80, required=False)
    university = forms.CharField(max_length=180, required=False)
    degree_programme = forms.CharField(
        max_length=180, required=False, label="Degree / programme"
    )
    field_of_study = forms.CharField(max_length=180, required=False)
    expected_graduation = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    languages = forms.CharField(
        required=False,
        help_text="Comma-separated, for example: Polish, English, German.",
    )
    linkedin_url = forms.URLField(max_length=300, required=False, label="LinkedIn URL")
    github_url = forms.URLField(max_length=300, required=False, label="GitHub URL")
    portfolio_url = forms.URLField(
        max_length=300, required=False, label="Portfolio URL"
    )
    preferred_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    preferred_end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    preferred_duration_weeks = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=104,
        label="Preferred duration (weeks)",
    )
    erasmus_declaration = forms.ChoiceField(
        choices=CandidateProfile.ErasmusDeclaration.choices,
        label="Erasmus+ eligibility / funding self-declaration",
        required=False,
    )

    class Meta:
        model = CandidateAccount
        fields = (
            "first_name",
            "last_name",
            "phone_number",
            "address_line",
            "city",
            "postal_code",
            "country_of_residence",
            "university",
            "degree_programme",
            "field_of_study",
            "expected_graduation",
            "languages",
            "linkedin_url",
            "github_url",
            "portfolio_url",
            "preferred_start_date",
            "preferred_end_date",
            "preferred_duration_weeks",
            "erasmus_declaration",
            "talent_pool_consent",
        )
        widgets = {
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
        }
        labels = {
            "talent_pool_consent": (
                "Contact me about other relevant THEVIPGROUP opportunities"
            )
        }

    PROFILE_FIELDS = (
        "phone_number",
        "address_line",
        "city",
        "postal_code",
        "country_of_residence",
        "university",
        "degree_programme",
        "field_of_study",
        "expected_graduation",
        "linkedin_url",
        "github_url",
        "portfolio_url",
        "preferred_start_date",
        "preferred_end_date",
        "preferred_duration_weeks",
        "erasmus_declaration",
    )

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile or CandidateProfile(account=self.instance)
        if not self.is_bound:
            for name in self.PROFILE_FIELDS:
                self.fields[name].initial = getattr(self.profile, name)
            self.fields["languages"].initial = ", ".join(self.profile.languages)

    def clean_phone_number(self):
        value = self.cleaned_data["phone_number"].strip()
        if value and (
            not value.replace(" ", "").lstrip("+").isdigit() or "+" in value[1:]
        ):
            raise ValidationError("Use digits, spaces and an optional leading + only.")
        return value

    def clean_languages(self):
        values = []
        for item in self.cleaned_data["languages"].split(","):
            item = item.strip()
            if item and item.casefold() not in {value.casefold() for value in values}:
                values.append(item)
        if len(values) > 20:
            raise ValidationError("List no more than 20 languages.")
        return values

    def clean_erasmus_declaration(self):
        return (
            self.cleaned_data["erasmus_declaration"]
            or self.profile.erasmus_declaration
            or CandidateProfile.ErasmusDeclaration.NOT_DECLARED
        )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("preferred_start_date")
        end = cleaned.get("preferred_end_date")
        if start and end and end < start:
            self.add_error(
                "preferred_end_date", "End date cannot be before start date."
            )
        return cleaned

    def save(self, commit=True):
        account = super().save(commit=False)
        if "talent_pool_consent" in self.changed_data:
            account.talent_pool_consent_updated_at = timezone.now()
        if commit:
            with transaction.atomic():
                account.save()
                for name in self.PROFILE_FIELDS:
                    setattr(self.profile, name, self.cleaned_data[name])
                self.profile.languages = self.cleaned_data["languages"]
                if "erasmus_declaration" in self.changed_data:
                    self.profile.erasmus_declaration_updated_at = timezone.now()
                self.profile.account = account
                self.profile.save()
        return account


class CandidateDocumentUploadForm(forms.Form):
    kind = forms.ChoiceField(choices=CandidateProfileDocument.Kind.choices)
    document = forms.FileField(
        help_text="PDF or DOCX, maximum 5 MB. Replacing a document keeps prior submissions unchanged."
    )

    def clean_document(self):
        upload = self.cleaned_data["document"]
        inspected = inspect_candidate_document(upload)
        self.content = inspected.content
        self.sha256 = inspected.sha256
        self.content_type = inspected.content_type
        return upload


class CandidateVerificationUploadForm(forms.Form):
    document = forms.FileField(
        help_text="Use the permitted format shown for this requirement; maximum 5 MB."
    )

    def __init__(self, *args, requirement, **kwargs):
        super().__init__(*args, **kwargs)
        self.requirement = requirement
        formats = [str(value).upper() for value in requirement["allowed_formats"]]
        self.fields["document"].help_text = (
            f"{', '.join(formats)}, maximum {requirement['max_size_mb']} MB. "
            "The document is sent directly to private PeopleOps verification storage."
        )

    def clean_document(self):
        upload = self.cleaned_data["document"]
        inspected = inspect_verification_document(
            upload,
            allowed_formats=self.requirement["allowed_formats"],
            max_size_mb=self.requirement["max_size_mb"],
        )
        self.content = inspected.content
        self.content_type = inspected.content_type
        return upload


class CandidateErasmusDocumentUploadForm(forms.Form):
    document = forms.FileField(
        help_text="PDF or DOCX, maximum 10 MB. The file is stored privately."
    )

    def clean_document(self):
        upload = self.cleaned_data["document"]
        inspect_candidate_document(
            upload,
            max_bytes=10 * 1024 * 1024,
            allowed_extensions=(".pdf", ".docx"),
        )
        return upload


class CandidateAccommodationForm(forms.Form):
    needs_assistance = forms.TypedChoiceField(
        choices=(("yes", "Yes"), ("no", "No")),
        coerce=lambda value: value == "yes",
        widget=forms.RadioSelect,
        label="Do you need assistance finding accommodation in Kraków?",
    )


class CandidateInterviewActionForm(forms.Form):
    slot_id = forms.UUIDField()
    candidate_timezone = forms.CharField(max_length=64)

    def clean_candidate_timezone(self):
        value = self.cleaned_data["candidate_timezone"].strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError(
                "Use a valid IANA timezone, for example Europe/Paris."
            ) from exc
        return value


class CandidateArrangementDatesForm(forms.Form):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot precede the start date.")
        return cleaned


class CandidateApplicationForm(forms.Form):
    confirmation = forms.BooleanField(
        label=(
            "I confirm that this application is accurate and I understand that "
            "a submission-time copy will be retained."
        )
    )

    def __init__(self, *args, job, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        for question in job.get("questionnaire", []):
            name = f"question_{question['id']}"
            common = {
                "label": question["label"],
                "required": bool(question.get("required")),
            }
            question_type = question.get("type")
            if question_type == "checkbox":
                field = forms.TypedChoiceField(
                    choices=(("true", "Yes"), ("false", "No")),
                    coerce=lambda value: str(value).casefold() == "true",
                    widget=forms.RadioSelect,
                    **common,
                )
            elif question_type == "options":
                field = forms.ChoiceField(
                    choices=[(item, item) for item in question.get("options", [])],
                    **common,
                )
            elif question_type == "multiple":
                field = forms.MultipleChoiceField(
                    choices=[(item, item) for item in question.get("options", [])],
                    widget=forms.CheckboxSelectMultiple,
                    **common,
                )
            elif question_type == "date":
                field = forms.DateField(
                    widget=forms.DateInput(attrs={"type": "date"}), **common
                )
            elif question_type == "textarea":
                field = forms.CharField(
                    max_length=4000, widget=forms.Textarea, **common
                )
            elif question_type == "rating":
                field = forms.IntegerField(min_value=1, max_value=5, **common)
            elif question_type == "percentage":
                field = forms.DecimalField(min_value=0, max_value=100, **common)
            elif question_type == "number":
                field = forms.DecimalField(**common)
            else:
                field = forms.CharField(max_length=4000, **common)
            self.fields[name] = field

    def questionnaire_snapshot(self):
        result = []
        for question in self.job.get("questionnaire", []):
            value = self.cleaned_data.get(f"question_{question['id']}")
            if value in (None, "", []):
                value = None
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            elif value is not None and not isinstance(value, (str, bool, list)):
                value = str(value)
            result.append(
                {
                    "id": question["id"],
                    "question": question["label"],
                    "type": question["type"],
                    "required": bool(question.get("required")),
                    "options": list(question.get("options", [])),
                    "answer": value,
                }
            )
        return result


class CandidatePasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        max_length=254,
        label="Personal email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )

    def clean_email(self):
        return normalize_candidate_email(self.cleaned_data["email"])


class CandidateSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
