from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from horilla.models import HorillaModel
from recruitment.models import Candidate, Recruitment, Skill
from tvg_careers.attribution import (
    APPLICATION_SOURCES,
    DEFAULT_APPLICATION_SOURCE,
    normalize_attribution,
)
from tvg_core.models import Person, Venture


class JobOpening(HorillaModel):
    """CareerHub presentation metadata for one Horilla recruitment position."""

    class OpportunityType(models.TextChoices):
        INTERNSHIP = "internship", _("Internship")
        JOB = "job", _("Job")

    class WorkMode(models.TextChoices):
        ONSITE = "onsite", _("On-site")
        HYBRID = "hybrid", _("Hybrid")
        REMOTE = "remote", _("Remote")

    class PublicationState(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")
        CLOSED = "closed", _("Closed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_slug = models.SlugField(max_length=160, unique=True)
    recruitment = models.OneToOneField(
        Recruitment,
        on_delete=models.PROTECT,
        related_name="tvg_job_opening",
        verbose_name=_("Horilla Recruitment"),
    )
    venture = models.ForeignKey(
        Venture,
        on_delete=models.PROTECT,
        related_name="job_openings",
        verbose_name=_("Recruiting Venture"),
    )
    opportunity_type = models.CharField(
        max_length=16,
        choices=OpportunityType.choices,
        default=OpportunityType.INTERNSHIP,
        db_index=True,
        verbose_name=_("Opportunity Type"),
    )
    responsibilities = models.TextField(verbose_name=_("Responsibilities"))
    learning_outcomes = models.TextField(verbose_name=_("Learning Outcomes"))
    preferred_skills = models.ManyToManyField(
        Skill,
        blank=True,
        related_name="preferred_for_tvg_openings",
        verbose_name=_("Preferred Skills"),
    )
    work_location = models.CharField(max_length=160, verbose_name=_("Location"))
    work_mode = models.CharField(
        max_length=16,
        choices=WorkMode.choices,
        default=WorkMode.HYBRID,
        verbose_name=_("Work Mode"),
    )
    erasmus_plus_applicable = models.BooleanField(
        default=False,
        verbose_name=_("Erasmus+ Applicable"),
    )
    expected_start_date_from = models.DateField(verbose_name=_("Earliest Start Date"))
    expected_start_date_to = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Latest Start Date"),
    )
    minimum_duration_weeks = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name=_("Minimum Duration (weeks)"),
    )
    preferred_duration_weeks = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name=_("Preferred Duration (weeks)"),
    )

    objects = HorillaCompanyManager("venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        permissions = (
            ("publish_jobopening", "Publish job opening"),
            ("close_jobopening", "Close job opening"),
        )
        verbose_name = _("CareerHub Job Opening")
        verbose_name_plural = _("CareerHub Job Openings")

    @property
    def title(self):
        return self.recruitment.title

    @property
    def role_description(self):
        return self.recruitment.description

    @property
    def job_position(self):
        return self.recruitment.job_position_id

    @property
    def department(self):
        position = self.job_position
        return position.department_id if position else None

    @property
    def required_skills(self):
        return self.recruitment.skills

    @property
    def number_of_positions(self):
        return self.recruitment.vacancy

    @property
    def hiring_managers(self):
        return self.recruitment.recruitment_managers

    @property
    def application_opening_date(self):
        return self.recruitment.start_date

    @property
    def application_closing_date(self):
        return self.recruitment.end_date

    @property
    def publication_state(self):
        if self.recruitment.closed or not self.recruitment.is_active:
            return self.PublicationState.CLOSED
        if self.recruitment.is_published:
            return self.PublicationState.PUBLISHED
        return self.PublicationState.DRAFT

    def is_publicly_available(self, on_date=None):
        on_date = on_date or timezone.localdate()
        return (
            self.is_active
            and self.publication_state == self.PublicationState.PUBLISHED
            and self.application_opening_date <= on_date
            and (
                self.application_closing_date is None
                or on_date <= self.application_closing_date
            )
        )

    def clean(self):
        super().clean()
        errors = {}
        if not self.recruitment_id or not self.venture_id:
            return
        recruitment = self.recruitment
        employer_id = self.venture.legal_employer_id

        if recruitment.is_event_based:
            errors["recruitment"] = _(
                "CareerHub openings require a dedicated non-event recruitment."
            )
        if recruitment.company_id_id != employer_id:
            errors["venture"] = _(
                "Recruiting venture and recruitment must share one legal employer."
            )
        if not recruitment.job_position_id_id:
            errors["recruitment"] = _("Recruitment must have one job position.")
        else:
            position = recruitment.job_position_id
            if not position.company_id.filter(pk=employer_id).exists():
                errors["recruitment"] = _(
                    "Job position must belong to the legal employer."
                )
            if not position.department_id.company_id.filter(pk=employer_id).exists():
                errors["recruitment"] = _(
                    "Department must belong to the legal employer."
                )

        if recruitment.pk:
            invalid_managers = recruitment.recruitment_managers.exclude(
                employee_work_info__company_id_id=employer_id
            )
            if invalid_managers.exists():
                errors["recruitment"] = _(
                    "Every hiring manager must belong to the legal employer."
                )

        if (
            self.expected_start_date_from
            and self.expected_start_date_to
            and (self.expected_start_date_to < self.expected_start_date_from)
        ):
            errors["expected_start_date_to"] = _(
                "Latest start date cannot be earlier than earliest start date."
            )
        if (
            self.minimum_duration_weeks is not None
            and self.preferred_duration_weeks is not None
            and self.preferred_duration_weeks < self.minimum_duration_weeks
        ):
            errors["preferred_duration_weeks"] = _(
                "Preferred duration cannot be shorter than minimum duration."
            )
        if recruitment.is_published and not recruitment.closed:
            if recruitment.end_date is None:
                errors["recruitment"] = _(
                    "Published openings require an application closing date."
                )
            if not (self.responsibilities or "").strip():
                errors["responsibilities"] = _(
                    "Published openings require responsibilities."
                )
            if not (self.learning_outcomes or "").strip():
                errors["learning_outcomes"] = _(
                    "Published openings require learning outcomes."
                )
            if not (self.work_location or "").strip():
                errors["work_location"] = _("Published openings require a location.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} — {self.venture.name}"


class RecruitmentQuestionnaireVersion(HorillaModel):
    """Immutable version of native Horilla survey configuration."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.PROTECT,
        related_name="tvg_questionnaire_versions",
    )
    version_number = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    definition_snapshot = models.JSONField(default=list)

    objects = HorillaCompanyManager("recruitment__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("recruitment_id", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("recruitment", "version_number"),
                name="tvg_careers_unique_questionnaire_version",
            ),
            models.UniqueConstraint(
                fields=("recruitment", "fingerprint"),
                name="tvg_careers_unique_questionnaire_fingerprint",
            ),
        ]
        verbose_name = _("CareerHub Questionnaire Version")
        verbose_name_plural = _("CareerHub Questionnaire Versions")

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Questionnaire versions are immutable."))
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.recruitment} v{self.version_number}"


class Application(HorillaModel):
    """Stable identity link around a Horilla Candidate application record."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="applications",
    )
    candidate = models.OneToOneField(
        Candidate,
        on_delete=models.PROTECT,
        related_name="tvg_application",
    )
    opening = models.ForeignKey(
        JobOpening,
        on_delete=models.PROTECT,
        related_name="applications",
    )
    submitted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    questionnaire_version = models.ForeignKey(
        RecruitmentQuestionnaireVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applications",
    )
    questionnaire_snapshot = models.JSONField(default=list, blank=True)
    source = models.CharField(
        max_length=32,
        choices=APPLICATION_SOURCES,
        default=DEFAULT_APPLICATION_SOURCE,
    )
    utm_parameters = models.JSONField(default=dict, blank=True)

    objects = HorillaCompanyManager("opening__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-submitted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("person", "opening"),
                name="tvg_careers_unique_person_per_opening",
            )
        ]
        permissions = (
            ("link_candidate_application", "Link candidate to person identity"),
        )
        verbose_name = _("CareerHub Application Link")
        verbose_name_plural = _("CareerHub Application Links")

    @property
    def stage(self):
        return self.candidate.stage_id

    @property
    def recruitment(self):
        return self.candidate.recruitment_id

    def clean(self):
        super().clean()
        errors = {}
        attribution = normalize_attribution(
            {"source": self.source, **(self.utm_parameters or {})}
        )
        self.source = attribution["source"]
        self.utm_parameters = attribution["utm_parameters"]
        if not self.person_id or not self.candidate_id or not self.opening_id:
            return
        employer_id = self.opening.venture.legal_employer_id
        if self.person.legal_employer_id != employer_id:
            errors["person"] = _(
                "Person and application must belong to the same legal employer."
            )
        if self.candidate.recruitment_id_id != self.opening.recruitment_id:
            errors["candidate"] = _(
                "Candidate must belong to the opening's Horilla recruitment."
            )
        expected_position_id = self.opening.recruitment.job_position_id_id
        if self.candidate.job_position_id_id != expected_position_id:
            errors["candidate"] = _(
                "Candidate job position must match the CareerHub opening."
            )
        if (
            self.questionnaire_version_id
            and self.questionnaire_version.recruitment_id != self.opening.recruitment_id
        ):
            errors["questionnaire_version"] = _(
                "Questionnaire version must belong to the opening's recruitment."
            )
        converted_employee_id = self.candidate.converted_employee_id_id
        if converted_employee_id and self.person.employee_id not in (
            None,
            converted_employee_id,
        ):
            errors["person"] = _(
                "Person identity is linked to a different employee record."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable_fields = (
                "person_id",
                "candidate_id",
                "opening_id",
                "submitted_at",
                "questionnaire_version_id",
                "questionnaire_snapshot",
                "source",
                "utm_parameters",
            )
            if original and any(
                getattr(original, field) != getattr(self, field)
                for field in immutable_fields
            ):
                raise ValidationError(_("Submitted application evidence is immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.person} — {self.opening.title}"


class ApplicationConfirmationEvent(HorillaModel):
    """Transactional outbox event emitted once after application creation."""

    EVENT_TYPE = "career.application_submitted.v1"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    application = models.OneToOneField(
        Application,
        on_delete=models.PROTECT,
        related_name="confirmation_event",
    )
    event_type = models.CharField(max_length=80, default=EVENT_TYPE, editable=False)
    schema_version = models.PositiveSmallIntegerField(default=1, editable=False)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager("application__opening__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)
        verbose_name = _("Application Confirmation Event")
        verbose_name_plural = _("Application Confirmation Events")

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable_fields = (
                "event_id",
                "application_id",
                "event_type",
                "schema_version",
                "payload",
                "occurred_at",
            )
            if original and any(
                getattr(original, field) != getattr(self, field)
                for field in immutable_fields
            ):
                raise ValidationError(_("Confirmation event payload is immutable."))
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"
