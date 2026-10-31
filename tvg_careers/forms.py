from django import forms
from django.db.models import Q

from employee.models import Employee
from recruitment.models import Skill, SurveyTemplate
from tvg_careers.models import Application, JobOpening
from tvg_careers.questionnaires import ensure_questionnaire_version


class JobOpeningAdminForm(forms.ModelForm):
    title = forms.CharField(max_length=50)
    role_description = forms.CharField(widget=forms.Textarea)
    required_skills = forms.ModelMultipleChoiceField(
        queryset=Skill.objects.all(),
        required=False,
    )
    number_of_positions = forms.IntegerField(min_value=1)
    hiring_managers = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.none(),
        required=False,
    )
    survey_templates = forms.ModelMultipleChoiceField(
        queryset=SurveyTemplate._base_manager.none(),
        required=False,
        help_text="Approved recruitment survey templates shown on CareerHub.",
    )
    application_opening_date = forms.DateField()
    application_closing_date = forms.DateField()
    publication_state = forms.ChoiceField(choices=JobOpening.PublicationState.choices)

    class Meta:
        model = JobOpening
        fields = (
            "venture",
            "recruitment",
            "opportunity_type",
            "public_slug",
            "responsibilities",
            "learning_outcomes",
            "preferred_skills",
            "work_location",
            "work_mode",
            "erasmus_plus_applicable",
            "expected_start_date_from",
            "expected_start_date_to",
            "minimum_duration_weeks",
            "preferred_duration_weeks",
            "is_active",
        )

    field_order = (
        "venture",
        "recruitment",
        "opportunity_type",
        "title",
        "public_slug",
        "publication_state",
        "role_description",
        "responsibilities",
        "learning_outcomes",
        "required_skills",
        "preferred_skills",
        "work_location",
        "work_mode",
        "erasmus_plus_applicable",
        "expected_start_date_from",
        "expected_start_date_to",
        "minimum_duration_weeks",
        "preferred_duration_weeks",
        "number_of_positions",
        "hiring_managers",
        "survey_templates",
        "application_opening_date",
        "application_closing_date",
        "is_active",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        recruitment = None
        if self.instance and self.instance.pk:
            recruitment = self.instance.recruitment
        elif self.is_bound and self.data.get("recruitment"):
            recruitment = (
                self.fields["recruitment"]
                .queryset.filter(pk=self.data.get("recruitment"))
                .first()
            )

        company_id = None
        if self.instance and self.instance.pk:
            company_id = self.instance.venture.legal_employer_id
        elif self.is_bound and self.data.get("venture"):
            venture = (
                self.fields["venture"]
                .queryset.filter(pk=self.data.get("venture"))
                .first()
            )
            company_id = venture.legal_employer_id if venture else None

        if company_id:
            self.fields["hiring_managers"].queryset = Employee.objects.filter(
                employee_work_info__company_id_id=company_id,
                is_active=True,
            )
            self.fields["survey_templates"].queryset = (
                SurveyTemplate._base_manager.filter(
                    Q(company_id_id=company_id)
                    | Q(company_id__isnull=True, is_general_template=True),
                    is_active=True,
                ).distinct()
            )

        if recruitment:
            self.fields["title"].initial = recruitment.title
            self.fields["role_description"].initial = recruitment.description
            self.fields["required_skills"].initial = recruitment.skills.all()
            self.fields["number_of_positions"].initial = recruitment.vacancy
            self.fields["hiring_managers"].initial = (
                recruitment.recruitment_managers.all()
            )
            self.fields["survey_templates"].initial = (
                SurveyTemplate._base_manager.filter(recruitment=recruitment)
            )
            self.fields["application_opening_date"].initial = recruitment.start_date
            self.fields["application_closing_date"].initial = recruitment.end_date
            if self.instance and self.instance.pk:
                self.fields["publication_state"].initial = (
                    self.instance.publication_state
                )

    def clean(self):
        cleaned = super().clean()
        opening_date = cleaned.get("application_opening_date")
        closing_date = cleaned.get("application_closing_date")
        if opening_date and closing_date and closing_date < opening_date:
            self.add_error(
                "application_closing_date",
                "Application closing date cannot be earlier than opening date.",
            )

        state = cleaned.get("publication_state")
        if state == JobOpening.PublicationState.PUBLISHED and not closing_date:
            self.add_error(
                "application_closing_date",
                "Published openings require an application closing date.",
            )

        venture = cleaned.get("venture")
        managers = cleaned.get("hiring_managers")
        if venture and managers is not None:
            invalid = managers.exclude(
                employee_work_info__company_id=venture.legal_employer
            )
            if invalid.exists():
                self.add_error(
                    "hiring_managers",
                    "Every hiring manager must belong to the legal employer.",
                )
        templates = cleaned.get("survey_templates")
        if venture and templates is not None:
            invalid = templates.exclude(
                Q(company_id=venture.legal_employer)
                | Q(company_id__isnull=True, is_general_template=True)
            )
            if invalid.exists():
                self.add_error(
                    "survey_templates",
                    "Every questionnaire template must belong to the legal employer.",
                )
        return cleaned

    def apply_recruitment_changes(self):
        """Persist fields whose authoritative owner is Horilla Recruitment."""

        recruitment = self.cleaned_data["recruitment"]
        recruitment.title = self.cleaned_data["title"]
        recruitment.description = self.cleaned_data["role_description"]
        recruitment.vacancy = self.cleaned_data["number_of_positions"]
        recruitment.start_date = self.cleaned_data["application_opening_date"]
        recruitment.end_date = self.cleaned_data["application_closing_date"]
        recruitment.full_clean()
        recruitment.save()
        recruitment.skills.set(self.cleaned_data["required_skills"])
        recruitment.recruitment_managers.set(self.cleaned_data["hiring_managers"])
        recruitment.survey_templates.set(self.cleaned_data["survey_templates"])
        ensure_questionnaire_version(recruitment)


class ApplicationAdminForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ("person", "candidate", "opening", "submitted_at", "is_active")
