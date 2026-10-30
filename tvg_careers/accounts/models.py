from __future__ import annotations

import uuid
from pathlib import Path

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from tvg_careers.attribution import (
    APPLICATION_SOURCES,
    DEFAULT_APPLICATION_SOURCE,
    normalize_attribution,
)


def normalize_candidate_email(value):
    return (value or "").strip().casefold()


def _private_document_path(instance, filename):
    extension = Path(filename).suffix.lower() or ".pdf"
    return f"candidate-documents/{instance.account_id}/{instance.id}{extension}"


def _application_snapshot_path(instance, filename):
    extension = Path(filename).suffix.lower() or ".pdf"
    return f"application-snapshots/{instance.account_id}/{instance.id}/{uuid.uuid4()}{extension}"


class CandidateAccountManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        email = normalize_candidate_email(email)
        if not email:
            raise ValueError("A personal email address is required.")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        raise ValueError("Public candidate accounts cannot be superusers.")


class CandidateAccount(AbstractBaseUser):
    """Credential principal for the isolated public CareerHub runtime."""

    class IdentityStatus(models.TextChoices):
        PENDING = "pending", "Pending email verification"
        LINKED = "linked", "Linked to PeopleOps identity"
        REVIEW_REQUIRED = "review_required", "Identity review required"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    person_public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    identity_status = models.CharField(
        max_length=24,
        choices=IdentityStatus.choices,
        default=IdentityStatus.PENDING,
        db_index=True,
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    privacy_notice_version = models.CharField(max_length=40)
    privacy_notice_acknowledged_at = models.DateTimeField(default=timezone.now)
    talent_pool_consent = models.BooleanField(default=False)
    talent_pool_consent_updated_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CandidateAccountManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ("email",)
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="tvg_candidate_account_email_ci_unique",
            )
        ]
        verbose_name = "CareerHub candidate account"
        verbose_name_plural = "CareerHub candidate accounts"

    def clean(self):
        super().clean()
        self.email = normalize_candidate_email(self.email)
        self.first_name = (self.first_name or "").strip()
        self.last_name = (self.last_name or "").strip()
        if not self.first_name:
            raise ValidationError({"first_name": "First name is required."})
        if not self.last_name:
            raise ValidationError({"last_name": "Last name is required."})

    def save(self, *args, **kwargs):
        self.email = normalize_candidate_email(self.email)
        return super().save(*args, **kwargs)

    @property
    def is_staff(self):
        return False

    @property
    def is_superuser(self):
        return False

    @property
    def applicant_functions_enabled(self):
        return bool(
            self.is_active
            and self.email_verified_at
            and self.identity_status == self.IdentityStatus.LINKED
        )

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def has_perm(self, perm, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False

    def __str__(self):
        return self.email


class CandidateProfile(models.Model):
    """Reusable candidate-supplied profile data owned by CareerHub."""

    class ErasmusDeclaration(models.TextChoices):
        ELIGIBLE_FUNDED = "eligible_funded", "Eligible and funding confirmed"
        ELIGIBLE_PLANNING = "eligible_planning", "Eligible; funding in progress"
        ELIGIBLE_UNFUNDED = "eligible_unfunded", "Eligible without confirmed funding"
        NOT_ELIGIBLE = "not_eligible", "Not eligible"
        UNSURE = "unsure", "Unsure"
        NOT_DECLARED = "not_declared", "Prefer not to declare"

    account = models.OneToOneField(
        CandidateAccount,
        on_delete=models.CASCADE,
        related_name="candidate_profile",
    )
    phone_number = models.CharField(max_length=32, blank=True)
    address_line = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=80, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country_of_residence = models.CharField(max_length=80, blank=True)
    university = models.CharField(max_length=180, blank=True)
    degree_programme = models.CharField(max_length=180, blank=True)
    field_of_study = models.CharField(max_length=180, blank=True)
    expected_graduation = models.DateField(null=True, blank=True)
    languages = models.JSONField(default=list, blank=True)
    linkedin_url = models.URLField(max_length=300, blank=True)
    github_url = models.URLField(max_length=300, blank=True)
    portfolio_url = models.URLField(max_length=300, blank=True)
    preferred_start_date = models.DateField(null=True, blank=True)
    preferred_end_date = models.DateField(null=True, blank=True)
    preferred_duration_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    erasmus_declaration = models.CharField(
        max_length=24,
        choices=ErasmusDeclaration.choices,
        default=ErasmusDeclaration.NOT_DECLARED,
    )
    erasmus_declaration_updated_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CareerHub candidate profile"

    def clean(self):
        super().clean()
        if self.preferred_start_date and self.preferred_end_date:
            if self.preferred_end_date < self.preferred_start_date:
                raise ValidationError(
                    {"preferred_end_date": "End date cannot be before start date."}
                )
        if not isinstance(self.languages, list):
            raise ValidationError({"languages": "Languages must be a list."})
        cleaned = []
        for language in self.languages:
            language = str(language).strip()
            if language and language.casefold() not in {
                item.casefold() for item in cleaned
            }:
                cleaned.append(language[:80])
        if len(cleaned) > 20:
            raise ValidationError({"languages": "List no more than 20 languages."})
        self.languages = cleaned

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class CandidateProfileDocument(models.Model):
    """Versioned private reusable document; only one current version per kind."""

    class Kind(models.TextChoices):
        CV = "cv", "CV"
        COVER_LETTER = "cover_letter", "Cover letter"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        CandidateAccount,
        on_delete=models.CASCADE,
        related_name="profile_documents",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to=_private_document_path, max_length=300)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, default="application/pdf")
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    is_current = models.BooleanField(default=True, db_index=True)
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("kind", "-uploaded_at")
        constraints = [
            models.UniqueConstraint(
                fields=("account", "kind"),
                condition=models.Q(is_current=True),
                name="tvg_careerhub_one_current_document_kind",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()} ({self.uploaded_at.date()})"


class CandidateApplication(models.Model):
    """CareerHub draft and immutable application-time submission snapshot."""

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        CandidateAccount,
        on_delete=models.PROTECT,
        related_name="candidate_applications",
    )
    opening_public_id = models.UUIDField()
    opening_slug = models.SlugField(max_length=160)
    opening_title = models.CharField(max_length=200)
    venture_slug = models.SlugField(max_length=80)
    venture_name = models.CharField(max_length=80)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    profile_snapshot = models.JSONField(default=dict, blank=True)
    questionnaire_snapshot = models.JSONField(default=list, blank=True)
    questionnaire_version_public_id = models.UUIDField(null=True, blank=True)
    questionnaire_version_number = models.PositiveIntegerField(null=True, blank=True)
    questionnaire_fingerprint = models.CharField(max_length=64, blank=True)
    cv_snapshot = models.FileField(
        upload_to=_application_snapshot_path,
        max_length=300,
        blank=True,
    )
    cv_original_name = models.CharField(max_length=255, blank=True)
    cv_sha256 = models.CharField(max_length=64, blank=True)
    cv_content_type = models.CharField(
        max_length=100,
        default="application/pdf",
    )
    cover_letter_snapshot = models.FileField(
        upload_to=_application_snapshot_path,
        max_length=300,
        blank=True,
    )
    cover_letter_original_name = models.CharField(max_length=255, blank=True)
    cover_letter_sha256 = models.CharField(max_length=64, blank=True)
    cover_letter_content_type = models.CharField(
        max_length=100,
        default="application/pdf",
    )
    source = models.CharField(
        max_length=32,
        choices=APPLICATION_SOURCES,
        default=DEFAULT_APPLICATION_SOURCE,
    )
    utm_parameters = models.JSONField(default=dict, blank=True)
    friendly_status_code = models.CharField(max_length=32, default="draft")
    friendly_status_label = models.CharField(max_length=80, default="Draft")
    submitted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    status_updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    IMMUTABLE_AFTER_SUBMISSION = (
        "account_id",
        "opening_public_id",
        "opening_slug",
        "opening_title",
        "venture_slug",
        "venture_name",
        "profile_snapshot",
        "questionnaire_snapshot",
        "questionnaire_version_public_id",
        "questionnaire_version_number",
        "questionnaire_fingerprint",
        "cv_snapshot",
        "cv_original_name",
        "cv_sha256",
        "cv_content_type",
        "cover_letter_snapshot",
        "cover_letter_original_name",
        "cover_letter_sha256",
        "cover_letter_content_type",
        "source",
        "utm_parameters",
        "submitted_at",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("account", "opening_public_id"),
                name="tvg_careerhub_one_application_per_opening",
            )
        ]

    def save(self, *args, **kwargs):
        attribution = normalize_attribution(
            {"source": self.source, **(self.utm_parameters or {})}
        )
        self.source = attribution["source"]
        self.utm_parameters = attribution["utm_parameters"]
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.state != self.State.DRAFT:
                changed = [
                    name
                    for name in self.IMMUTABLE_AFTER_SUBMISSION
                    if getattr(original, name) != getattr(self, name)
                ]
                if changed:
                    raise ValidationError(
                        "Submitted application snapshots cannot be changed."
                    )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.opening_title} ({self.get_state_display()})"


class CandidateApplicationStatusEvent(models.Model):
    """Candidate-safe history mirrored from PeopleOps status changes."""

    application = models.ForeignKey(
        CandidateApplication,
        on_delete=models.CASCADE,
        related_name="status_events",
    )
    code = models.CharField(max_length=32)
    label = models.CharField(max_length=80)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-occurred_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("application", "code", "occurred_at"),
                name="tvg_careerhub_unique_application_status_event",
            )
        ]


class CandidateSecurityEvent(models.Model):
    """Data-minimized audit record for candidate account security events."""

    class EventType(models.TextChoices):
        ACCOUNT_CREATED = "account_created", "Account created"
        VERIFICATION_SENT = "verification_sent", "Verification email sent"
        EMAIL_VERIFIED = "email_verified", "Email verified"
        IDENTITY_REVIEW = "identity_review", "Identity review required"
        LOGIN_SUCCEEDED = "login_succeeded", "Login succeeded"
        LOGIN_FAILED = "login_failed", "Login failed"
        LOGOUT = "logout", "Logout"
        RECOVERY_REQUESTED = "recovery_requested", "Recovery requested"
        PASSWORD_RESET = "password_reset", "Password reset"
        PROFILE_UPDATED = "profile_updated", "Profile updated"
        DOCUMENT_UPLOADED = "document_uploaded", "Document uploaded"
        APPLICATION_SUBMITTED = "application_submitted", "Application submitted"
        APPLICATION_WITHDRAWN = "application_withdrawn", "Application withdrawn"
        VERIFICATION_UPLOADED = "verification_uploaded", "Verification uploaded"
        INTERVIEW_BOOKED = "interview_booked", "Interview booked"
        INTERVIEW_RESCHEDULED = "interview_rescheduled", "Interview rescheduled"
        ARRANGEMENT_DATES_CONFIRMED = (
            "arrangement_dates_confirmed",
            "Arrangement dates confirmed",
        )
        ACCEPTANCE_DOWNLOADED = "acceptance_downloaded", "Acceptance downloaded"
        ACCEPTANCE_RECEIVED = "acceptance_received", "Acceptance receipt confirmed"

    account = models.ForeignKey(
        CandidateAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="security_events",
    )
    event_type = models.CharField(
        max_length=32, choices=EventType.choices, db_index=True
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_fingerprint = models.CharField(max_length=64, blank=True)
    user_agent_fingerprint = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-occurred_at",)
        verbose_name = "Candidate security event"
        verbose_name_plural = "Candidate security events"

    def __str__(self):
        return f"{self.event_type} at {self.occurred_at.isoformat()}"
