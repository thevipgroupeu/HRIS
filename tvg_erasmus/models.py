from __future__ import annotations

import os
import uuid
from string import Formatter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department
from employee.models import Employee
from horilla.models import HorillaModel
from recruitment.models import (
    Candidate,
    CandidateDocument,
    CandidateDocumentRequest,
    Recruitment,
)
from tvg_careers.models import Application
from tvg_careers.verification_document_security import ALLOWED_VERIFICATION_FORMATS
from tvg_erasmus.storage import (
    AcceptancePrivateStorage,
    ErasmusDocumentPrivateStorage,
    VerificationPrivateStorage,
)


def verification_evidence_path(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    return (
        f"cases/{instance.requirement.case.public_id}/"
        f"{instance.requirement.public_id}/{instance.public_id}{extension}"
    )


def acceptance_letter_path(instance, filename):
    """Keep document paths opaque and independent from candidate identifiers."""

    return f"letters/{instance.public_id}/{uuid.uuid4()}.pdf"


def erasmus_document_template_path(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    return f"templates/{instance.public_id}/{uuid.uuid4()}{extension}"


def erasmus_document_version_path(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    return (
        f"case-documents/{instance.document.case.public_id}/"
        f"{instance.document.public_id}/{instance.public_id}{extension}"
    )


class ScreeningDecision(HorillaModel):
    class Status(models.TextChoices):
        INITIAL_SCREENING = "initial_screening", _("Initial HR screening")
        SHORTLISTED = "shortlisted", _("Shortlisted")
        REJECTED = "rejected", _("Rejected")
        HOLD = "hold", _("On hold")
        INFORMATION_REQUESTED = "information_requested", _("Information requested")

    application = models.OneToOneField(
        Application,
        on_delete=models.PROTECT,
        related_name="screening",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.INITIAL_SCREENING,
    )
    applicant_message = models.CharField(max_length=500, blank=True)
    internal_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_screening_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("application__opening__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        permissions = (("screen_application", "Screen CareerHub applications"),)
        ordering = ("-application__submitted_at",)

    def __str__(self):
        return f"{self.application} - {self.get_status_display()}"


class ScreeningEvent(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    screening = models.ForeignKey(
        ScreeningDecision,
        on_delete=models.PROTECT,
        related_name="events",
    )
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(
        max_length=32, choices=ScreeningDecision.Status.choices
    )
    actor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_screening_events",
    )
    applicant_message = models.CharField(max_length=500, blank=True)
    internal_note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "screening__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Screening events are immutable."))
        return super().save(*args, **kwargs)


class VerificationChecklist(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recruitment = models.OneToOneField(
        Recruitment,
        on_delete=models.PROTECT,
        related_name="tvg_verification_checklist",
    )
    title = models.CharField(max_length=120, default="Pre-interview verification")
    due_days = models.PositiveSmallIntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    reminder_interval_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    retention_days = models.PositiveIntegerField(
        default=90,
        validators=[MinValueValidator(1), MaxValueValidator(730)],
        help_text=_(
            "Delete verification evidence this many days after case completion."
        ),
    )

    objects = HorillaCompanyManager("recruitment__company_id")
    all_objects = models.Manager()

    class Meta:
        permissions = (("configure_verification", "Configure verification checklists"),)

    def __str__(self):
        return f"{self.recruitment} - {self.title}"


class VerificationRequirementTemplate(HorillaModel):
    class Category(models.TextChoices):
        IDENTITY = "identity", _("Identity verification")
        STUDENT_STATUS = "student_status", _("Student-status confirmation")
        UNIVERSITY = "university", _("University confirmation")
        OTHER = "other", _("Other")

    checklist = models.ForeignKey(
        VerificationChecklist,
        on_delete=models.CASCADE,
        related_name="requirement_templates",
    )
    code = models.SlugField(max_length=50)
    category = models.CharField(max_length=32, choices=Category.choices)
    title = models.CharField(max_length=120)
    instructions = models.TextField()
    required = models.BooleanField(default=True)
    allowed_formats = models.JSONField(default=list)
    max_size_mb = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    sequence = models.PositiveSmallIntegerField(default=0)

    objects = HorillaCompanyManager("checklist__recruitment__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("sequence", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("checklist", "code"),
                name="tvg_erasmus_unique_checklist_requirement_code",
            )
        ]

    def clean(self):
        super().clean()
        formats = [str(value).lower() for value in self.allowed_formats]
        if not formats or any(
            value not in ALLOWED_VERIFICATION_FORMATS for value in formats
        ):
            raise ValidationError(
                {"allowed_formats": _("Use only PDF, JPG, JPEG or PNG formats.")}
            )
        if len(formats) != len(set(formats)):
            raise ValidationError({"allowed_formats": _("Formats must be unique.")})
        self.allowed_formats = formats

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class VerificationCase(HorillaModel):
    class Status(models.TextChoices):
        NOT_REQUESTED = "not_requested", _("Not requested")
        REQUESTED = "requested", _("Requested")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under review")
        REVISION_NEEDED = "revision_needed", _("Revision needed")
        VERIFIED = "verified", _("Verified")
        REJECTED = "rejected", _("Rejected")
        NOT_REQUIRED = "not_required", _("Not required")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    application = models.OneToOneField(
        Application,
        on_delete=models.PROTECT,
        related_name="verification_case",
    )
    checklist = models.ForeignKey(
        VerificationChecklist,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cases",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.NOT_REQUESTED,
    )
    requested_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="requested_verification_cases",
    )
    due_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_verification_cases",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("application__opening__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        permissions = (
            ("manage_verification", "Request and manage verification"),
            ("review_verification", "Review verification evidence"),
            ("send_verification_reminder", "Send verification reminders"),
        )
        ordering = ("-requested_at",)

    def __str__(self):
        return f"{self.application} - {self.get_status_display()}"


class VerificationRequirement(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        VerificationCase,
        on_delete=models.PROTECT,
        related_name="requirements",
    )
    template = models.ForeignKey(
        VerificationRequirementTemplate,
        on_delete=models.PROTECT,
        related_name="instances",
    )
    native_document_request = models.OneToOneField(
        CandidateDocumentRequest,
        on_delete=models.PROTECT,
        related_name="tvg_verification_requirement",
    )
    native_document = models.OneToOneField(
        CandidateDocument,
        on_delete=models.PROTECT,
        related_name="tvg_verification_requirement",
    )
    code = models.SlugField(max_length=50)
    category = models.CharField(
        max_length=32,
        choices=VerificationRequirementTemplate.Category.choices,
    )
    title = models.CharField(max_length=120)
    instructions = models.TextField()
    required = models.BooleanField(default=True)
    allowed_formats = models.JSONField(default=list)
    max_size_mb = models.PositiveSmallIntegerField(default=5)
    status = models.CharField(
        max_length=24,
        choices=VerificationCase.Status.choices,
        default=VerificationCase.Status.REQUESTED,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_verification_requirements",
    )
    revision_reason = models.CharField(max_length=500, blank=True)
    internal_note = models.TextField(blank=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("template__sequence", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("case", "code"),
                name="tvg_erasmus_unique_case_requirement_code",
            )
        ]

    def __str__(self):
        return f"{self.case} - {self.title}"


class VerificationEvidence(HorillaModel):
    class ScanStatus(models.TextChoices):
        PENDING = "pending", _("Pending scan")
        CLEAN = "clean", _("Clean")
        INFECTED = "infected", _("Rejected by scanner")
        ERROR = "error", _("Scanner error")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requirement = models.ForeignKey(
        VerificationRequirement,
        on_delete=models.PROTECT,
        related_name="evidence_versions",
    )
    version_number = models.PositiveIntegerField()
    file = models.FileField(
        upload_to=verification_evidence_path,
        storage=VerificationPrivateStorage(),
        max_length=500,
    )
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(
        max_length=16,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    scan_reference = models.CharField(max_length=120, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    delete_after = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "requirement__case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("requirement_id", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("requirement", "version_number"),
                name="tvg_erasmus_unique_evidence_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "requirement_id",
                "version_number",
                "file",
                "original_name",
                "content_type",
                "size_bytes",
                "sha256",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(
                    _("Submitted verification evidence is immutable.")
                )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.requirement} v{self.version_number}"


class VerificationAuditEvent(HorillaModel):
    class ActorType(models.TextChoices):
        CANDIDATE = "candidate", _("Candidate")
        STAFF = "staff", _("Staff")
        SYSTEM = "system", _("System")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        VerificationCase,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    requirement = models.ForeignKey(
        VerificationRequirement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=64)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_reference = models.UUIDField(null=True, blank=True)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    candidate_message = models.CharField(max_length=500, blank=True)
    internal_note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Verification audit events are immutable."))
        return super().save(*args, **kwargs)


class VerificationNotification(HorillaModel):
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        VerificationCase,
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    requirement = models.ForeignKey(
        VerificationRequirement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=160, unique=True)
    candidate_message = models.CharField(max_length=500)
    next_action = models.CharField(max_length=300, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "event_id",
                "case_id",
                "requirement_id",
                "event_type",
                "idempotency_key",
                "candidate_message",
                "next_action",
                "occurred_at",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(_("Notification event payload is immutable."))
        return super().save(*args, **kwargs)


class ErasmusArrangementPolicy(HorillaModel):
    """Legal-employer policy used when validating selected-candidate cases."""

    company = models.OneToOneField(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_erasmus_arrangement_policy",
    )
    minimum_duration_months = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text=_(
            "Normal minimum internship duration in calendar months. Shorter "
            "arrangements require an approved exception."
        ),
    )
    letter_number_prefix = models.CharField(
        max_length=40,
        blank=True,
        help_text=_(
            "Organization-approved prefix used for acceptance-letter numbering."
        ),
    )
    next_letter_sequence = models.PositiveIntegerField(default=1)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        permissions = (
            ("configure_arrangement_policy", "Configure Erasmus arrangement policy"),
        )

    def clean(self):
        super().clean()
        self.letter_number_prefix = self.letter_number_prefix.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company} Erasmus arrangements"


class ErasmusCase(HorillaModel):
    """Approved-arrangement aggregate linked to one selected application."""

    class FundingDeclaration(models.TextChoices):
        ELIGIBLE_FUNDED = "eligible_funded", _("Eligible and funding confirmed")
        ELIGIBLE_PLANNING = "eligible_planning", _("Eligible; funding in progress")
        ELIGIBLE_UNFUNDED = "eligible_unfunded", _("Eligible without confirmed funding")
        NOT_ELIGIBLE = "not_eligible", _("Not eligible")
        UNSURE = "unsure", _("Unsure")
        NOT_DECLARED = "not_declared", _("Not declared")

    class UniversityConfirmation(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        REVISION_NEEDED = "revision_needed", _("Revision needed")

    class ArrangementStatus(models.TextChoices):
        PROPOSED = "proposed", _("Proposed")
        CANDIDATE_CONFIRMED = "candidate_confirmed", _("Candidate confirmed")
        HR_APPROVED = "hr_approved", _("HR approved")

    class AcceptanceStatus(models.TextChoices):
        NOT_READY = "not_ready", _("Not ready")
        READY = "ready", _("Ready to issue")
        ISSUED = "issued", _("Issued")
        SENT = "sent", _("Sent")
        RECEIVED = "received", _("Receipt confirmed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    application = models.OneToOneField(
        Application,
        on_delete=models.PROTECT,
        related_name="erasmus_case",
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.PROTECT,
        related_name="tvg_erasmus_cases",
    )
    selection_decision = models.OneToOneField(
        "tvg_interviews.InterviewDecision",
        on_delete=models.PROTECT,
        related_name="erasmus_case",
    )
    university = models.CharField(max_length=180)
    programme = models.CharField(max_length=180)
    funding_declaration = models.CharField(
        max_length=24,
        choices=FundingDeclaration.choices,
        default=FundingDeclaration.NOT_DECLARED,
    )
    proposed_start_date = models.DateField()
    proposed_end_date = models.DateField()
    confirmed_start_date = models.DateField(null=True, blank=True)
    confirmed_end_date = models.DateField(null=True, blank=True)
    internship_duration_days = models.PositiveIntegerField(default=0, editable=False)
    working_model = models.CharField(
        max_length=16,
        choices=(
            ("onsite", _("On-site")),
            ("hybrid", _("Hybrid")),
            ("remote", _("Remote")),
        ),
    )
    work_location = models.CharField(max_length=180)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="tvg_erasmus_cases",
    )
    supervisor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="supervised_tvg_erasmus_cases",
    )
    curriculum_reference = models.CharField(max_length=120)
    curriculum_version = models.CharField(max_length=40)
    university_confirmation_status = models.CharField(
        max_length=24,
        choices=UniversityConfirmation.choices,
        default=UniversityConfirmation.PENDING,
    )
    arrangement_status = models.CharField(
        max_length=24,
        choices=ArrangementStatus.choices,
        default=ArrangementStatus.PROPOSED,
    )
    acceptance_status = models.CharField(
        max_length=16,
        choices=AcceptanceStatus.choices,
        default=AcceptanceStatus.NOT_READY,
    )
    outstanding_requirements = models.JSONField(default=list, blank=True)
    candidate_confirmed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_erasmus_cases",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_snapshot = models.JSONField(default=dict, blank=True)
    before_mobility_completed_at = models.DateTimeField(null=True, blank=True)
    before_mobility_completed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_tvg_before_mobility_cases",
    )
    document_requirement_snapshot = models.JSONField(default=list, blank=True)

    objects = HorillaCompanyManager("application__opening__venture__legal_employer")
    all_objects = models.Manager()

    IMMUTABLE_AFTER_APPROVAL = (
        "application_id",
        "candidate_id",
        "selection_decision_id",
        "university",
        "programme",
        "funding_declaration",
        "proposed_start_date",
        "proposed_end_date",
        "confirmed_start_date",
        "confirmed_end_date",
        "internship_duration_days",
        "working_model",
        "work_location",
        "department_id",
        "supervisor_id",
        "curriculum_reference",
        "curriculum_version",
        "university_confirmation_status",
        "approved_by_id",
        "approved_at",
        "approval_snapshot",
    )

    class Meta:
        ordering = ("-created_at",)
        permissions = (
            ("manage_erasmus_case", "Create and edit Erasmus arrangements"),
            ("approve_erasmus_case", "Approve Erasmus arrangements"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.proposed_start_date and self.proposed_end_date:
            if self.proposed_end_date < self.proposed_start_date:
                errors["proposed_end_date"] = _(
                    "Proposed end date cannot precede the start date."
                )
        if bool(self.confirmed_start_date) != bool(self.confirmed_end_date):
            errors["confirmed_end_date"] = _(
                "Both confirmed dates must be provided together."
            )
        if self.confirmed_start_date and self.confirmed_end_date:
            if self.confirmed_end_date < self.confirmed_start_date:
                errors["confirmed_end_date"] = _(
                    "Confirmed end date cannot precede the start date."
                )
        if self.application_id and self.candidate_id:
            if self.application.candidate_id != self.candidate_id:
                errors["candidate"] = _(
                    "The candidate must be the application candidate."
                )
        if self.application_id and self.selection_decision_id:
            if (
                self.selection_decision.booking.invitation.application_id
                != self.application_id
            ):
                errors["selection_decision"] = _(
                    "The selection decision must belong to this application."
                )
            if self.selection_decision.outcome != "selected":
                errors["selection_decision"] = _(
                    "An Erasmus case requires a selected interview decision."
                )
        if self.application_id and self.department_id:
            employer_id = self.application.opening.venture.legal_employer_id
            if not self.department.company_id.filter(pk=employer_id).exists():
                errors["department"] = _(
                    "Department must belong to the legal employer."
                )
        if self.application_id and self.supervisor_id:
            if (
                self.supervisor.get_company()
                != self.application.opening.venture.legal_employer
            ):
                errors["supervisor"] = _(
                    "Supervisor must belong to the legal employer."
                )
        if not isinstance(self.outstanding_requirements, list):
            errors["outstanding_requirements"] = _(
                "Outstanding requirements must be a list."
            )
        if not isinstance(self.document_requirement_snapshot, list):
            errors["document_requirement_snapshot"] = _(
                "Document requirement snapshot must be a list."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if (
                original
                and original.approved_at
                and any(
                    getattr(original, field) != getattr(self, field)
                    for field in self.IMMUTABLE_AFTER_APPROVAL
                )
            ):
                raise ValidationError(
                    _(
                        "Approved arrangements are immutable; use a formal amendment workflow."
                    )
                )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.application} - Erasmus arrangements"


class DurationException(HorillaModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        ErasmusCase,
        on_delete=models.PROTECT,
        related_name="duration_exceptions",
    )
    version_number = models.PositiveIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    minimum_duration_months = models.PositiveSmallIntegerField()
    reason = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="requested_tvg_duration_exceptions",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_tvg_duration_exceptions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("case_id", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("case", "version_number"),
                name="tvg_erasmus_unique_duration_exception_version",
            ),
            models.UniqueConstraint(
                fields=("case",),
                condition=models.Q(status="pending"),
                name="tvg_erasmus_one_pending_duration_exception",
            ),
        ]
        permissions = (
            ("request_duration_exception", "Request a duration exception"),
            ("approve_duration_exception", "Approve a duration exception"),
        )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.status != self.Status.PENDING:
                raise ValidationError(_("Duration-exception decisions are immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)


class AcceptanceLetterTemplate(HorillaModel):
    """Versioned plain-text template containing explicit legal issuer details."""

    ALLOWED_PLACEHOLDERS = {
        "candidate_name",
        "role_title",
        "venture_name",
        "university",
        "programme",
        "confirmed_start_date",
        "confirmed_end_date",
        "internship_duration_days",
        "working_model",
        "work_location",
        "department",
        "supervisor",
        "curriculum_reference",
        "curriculum_version",
        "letter_number",
        "issue_date",
        "legal_entity_name",
        "legal_address",
        "registration_details",
        "authorized_signatory_name",
        "authorized_signatory_title",
    }

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_acceptance_letter_templates",
    )
    name = models.CharField(max_length=120)
    version_number = models.PositiveIntegerField(default=1)
    body = models.TextField(
        help_text=_(
            "Plain text only. Placeholders use {candidate_name} syntax and are "
            "validated against the approved allowlist."
        )
    )
    legal_entity_name = models.CharField(max_length=200)
    legal_address = models.TextField(max_length=500)
    registration_details = models.TextField(max_length=500)
    authorized_signatory_name = models.CharField(max_length=200)
    authorized_signatory_title = models.CharField(max_length=160)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_acceptance_templates",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("company", "name", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "name", "version_number"),
                name="tvg_erasmus_unique_acceptance_template_version",
            )
        ]
        permissions = (
            ("approve_acceptance_template", "Approve acceptance-letter templates"),
        )

    def clean(self):
        super().clean()
        errors = {}
        try:
            placeholders = {
                name
                for _literal, name, _spec, _conversion in Formatter().parse(self.body)
                if name
            }
        except ValueError:
            errors["body"] = _("Template placeholders are malformed.")
        else:
            if placeholders - self.ALLOWED_PLACEHOLDERS:
                errors["body"] = _("The template contains an unsupported placeholder.")
        required_text = (
            "body",
            "legal_entity_name",
            "legal_address",
            "registration_details",
            "authorized_signatory_name",
            "authorized_signatory_title",
        )
        for field in required_text:
            if not getattr(self, field, "").strip():
                errors[field] = _("This explicit company-authored value is required.")
        if self.approved_by_id and self.approved_by.get_company() != self.company:
            errors["approved_by"] = _("Approver must belong to the legal employer.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "name",
                    "version_number",
                    "body",
                    "legal_entity_name",
                    "legal_address",
                    "registration_details",
                    "authorized_signatory_name",
                    "authorized_signatory_title",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _(
                            "Approved templates are immutable; create a new template version."
                        )
                    )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version_number}"


class AcceptanceLetter(HorillaModel):
    class Status(models.TextChoices):
        ISSUED = "issued", _("Issued")
        SENT = "sent", _("Sent")
        RECEIVED = "received", _("Receipt confirmed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        ErasmusCase,
        on_delete=models.PROTECT,
        related_name="acceptance_letters",
    )
    template = models.ForeignKey(
        AcceptanceLetterTemplate,
        on_delete=models.PROTECT,
        related_name="letters",
    )
    document_version = models.PositiveIntegerField()
    letter_number = models.CharField(max_length=100, unique=True)
    issue_date = models.DateField()
    rendered_text = models.TextField()
    pdf_file = models.FileField(
        upload_to=acceptance_letter_path,
        storage=AcceptancePrivateStorage(),
        max_length=500,
    )
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ISSUED,
    )
    authorized_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="authorized_tvg_acceptance_letters",
    )
    authorized_at = models.DateTimeField(default=timezone.now)
    sent_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sent_tvg_acceptance_letters",
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by_person_public_id = models.UUIDField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("case_id", "-document_version")
        constraints = [
            models.UniqueConstraint(
                fields=("case", "document_version"),
                name="tvg_erasmus_unique_acceptance_letter_version",
            )
        ]
        permissions = (
            ("issue_acceptance_letter", "Authorize and issue acceptance letters"),
            ("send_acceptance_letter", "Send issued acceptance letters"),
        )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "case_id",
                "template_id",
                "document_version",
                "letter_number",
                "issue_date",
                "rendered_text",
                "pdf_file",
                "sha256",
                "size_bytes",
                "authorized_by_id",
                "authorized_at",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(_("Issued acceptance documents are immutable."))
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.letter_number} v{self.document_version}"


class ErasmusCaseEvent(HorillaModel):
    class ActorType(models.TextChoices):
        STAFF = "staff", _("Staff")
        CANDIDATE = "candidate", _("Candidate")
        SYSTEM = "system", _("System")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        ErasmusCase,
        on_delete=models.PROTECT,
        related_name="events",
    )
    event_type = models.CharField(max_length=64)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_erasmus_case_events",
    )
    actor_person_public_id = models.UUIDField(null=True, blank=True)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Erasmus case events are immutable."))
        return super().save(*args, **kwargs)


class AcceptanceNotification(HorillaModel):
    """Unsent outbox event; delivery belongs to the future automation boundary."""

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        ErasmusCase,
        on_delete=models.PROTECT,
        related_name="acceptance_notifications",
    )
    letter = models.ForeignKey(
        AcceptanceLetter,
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=180, unique=True)
    candidate_message = models.CharField(max_length=500)
    occurred_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "event_id",
                "case_id",
                "letter_id",
                "event_type",
                "idempotency_key",
                "candidate_message",
                "occurred_at",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(_("Acceptance event payloads are immutable."))
        return super().save(*args, **kwargs)


class ErasmusDocumentTemplate(HorillaModel):
    """Human-approved source file; approved versions cannot be mutated."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_erasmus_document_templates",
    )
    name = models.CharField(max_length=160)
    version_number = models.PositiveIntegerField()
    document_type = models.CharField(max_length=32)
    university = models.CharField(max_length=180, blank=True)
    programme = models.CharField(max_length=180, blank=True)
    source_file = models.FileField(
        upload_to=erasmus_document_template_path,
        storage=ErasmusDocumentPrivateStorage(),
        max_length=500,
    )
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_erasmus_document_templates",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("name", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "name", "version_number"),
                name="tvg_erasmus_unique_document_template_version",
            )
        ]
        permissions = (
            ("configure_document_templates", "Configure Erasmus document templates"),
            ("approve_document_template", "Approve Erasmus document templates"),
        )

    def clean(self):
        super().clean()
        allowed = {
            "learning_agreement",
            "nda",
            "code_of_conduct",
            "internship_agreement",
            "other",
        }
        if self.document_type not in allowed:
            raise ValidationError({"document_type": _("Unsupported document type.")})

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            protected = (
                "company_id",
                "name",
                "version_number",
                "document_type",
                "university",
                "programme",
                "source_file",
                "content_type",
                "sha256",
                "size_bytes",
                "approved_by_id",
                "approved_at",
            )
            if (
                original
                and original.approved_at
                and any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                )
            ):
                raise ValidationError(
                    _("Approved templates are immutable; create a new version.")
                )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version_number}"


class ErasmusDocumentRequirement(HorillaModel):
    """Approved, university-specific configuration copied into each case."""

    class DocumentType(models.TextChoices):
        LEARNING_AGREEMENT = "learning_agreement", _("Learning Agreement")
        NDA = "nda", _("NDA")
        CODE_OF_CONDUCT = "code_of_conduct", _("Code of Conduct")
        INTERNSHIP_AGREEMENT = "internship_agreement", _("Internship Agreement")
        OTHER = "other", _("Other approved document")

    class Phase(models.TextChoices):
        BEFORE_MOBILITY = "before_mobility", _("Before Mobility")
        DURING_MOBILITY = "during_mobility", _("During Mobility")
        AFTER_INTERNSHIP = "after_internship", _("After internship")

    class SignerRole(models.TextChoices):
        STUDENT = "student", _("Student")
        UNIVERSITY = "university", _("University")
        TVG_AUTHORIZED_REPRESENTATIVE = "tvg_authorized_representative", _(
            "THEVIPGROUP authorized representative"
        )
        SUPERVISOR = "supervisor", _("Supervisor")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_erasmus_document_requirements",
    )
    code = models.SlugField(max_length=64)
    version_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=160)
    document_type = models.CharField(max_length=32, choices=DocumentType.choices)
    lifecycle_phase = models.CharField(max_length=24, choices=Phase.choices)
    university = models.CharField(max_length=180, blank=True)
    programme = models.CharField(max_length=180, blank=True)
    instructions = models.TextField(blank=True)
    required = models.BooleanField(default=True)
    signer_roles = models.JSONField(default=list, blank=True)
    approved_template = models.ForeignKey(
        ErasmusDocumentTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="requirements",
    )
    external_system = models.CharField(
        max_length=32,
        blank=True,
        help_text=_(
            "Optional integration key, for example 'na10'; no rules are inferred."
        ),
    )
    external_requirement_code = models.CharField(max_length=100, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_erasmus_document_requirements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("lifecycle_phase", "code")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "company",
                    "university",
                    "programme",
                    "code",
                    "version_number",
                ),
                name="tvg_erasmus_unique_document_requirement_scope",
            )
        ]
        permissions = (
            (
                "configure_document_requirements",
                "Configure Erasmus document requirements",
            ),
            ("approve_document_requirement", "Approve Erasmus document requirements"),
        )

    def clean(self):
        super().clean()
        errors = {}
        allowed_roles = {value for value, _label in self.SignerRole.choices}
        if not isinstance(self.signer_roles, list) or len(self.signer_roles) != len(
            set(self.signer_roles)
        ):
            errors["signer_roles"] = _("Signer roles must be a unique list.")
        elif any(role not in allowed_roles for role in self.signer_roles):
            errors["signer_roles"] = _("Signer roles contain an unsupported role.")
        if (
            self.document_type == self.DocumentType.LEARNING_AGREEMENT
            and self.SignerRole.TVG_AUTHORIZED_REPRESENTATIVE not in self.signer_roles
        ):
            errors["signer_roles"] = _(
                "A Learning Agreement requires an authorized company representative."
            )
        if (
            self.document_type != self.DocumentType.LEARNING_AGREEMENT
            and not self.signer_roles
        ):
            errors["signer_roles"] = _(
                "Company legal documents require configured human signer roles."
            )
        if self.approved_template_id:
            if self.approved_template.company_id != self.company_id:
                errors["approved_template"] = _("Template must belong to this company.")
            elif not self.approved_template.approved_at:
                errors["approved_template"] = _("Template must be approved first.")
            elif self.approved_template.document_type != self.document_type:
                errors["approved_template"] = _(
                    "Template and requirement document types must match."
                )
        elif self.document_type != self.DocumentType.LEARNING_AGREEMENT:
            errors["approved_template"] = _(
                "Company legal-document requirements need an approved template."
            )
        if bool(self.external_system) != bool(self.external_requirement_code):
            errors["external_requirement_code"] = _(
                "External system and requirement code must be configured together."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                raise ValidationError(
                    _(
                        "Approved requirements are immutable; create a new configuration."
                    )
                )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} v{self.version_number}"


class ErasmusCaseDocument(HorillaModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        SUBMITTED = "submitted", _("Submitted")
        REVIEWED = "reviewed", _("Reviewed")
        REVISION_NEEDED = "revision_needed", _("Revision needed")
        APPROVED = "approved", _("Approved")
        SIGNED = "signed", _("Signed")
        RETURNED = "returned", _("Returned")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        ErasmusCase, on_delete=models.PROTECT, related_name="documents"
    )
    requirement = models.ForeignKey(
        ErasmusDocumentRequirement,
        on_delete=models.PROTECT,
        related_name="case_documents",
    )
    template = models.ForeignKey(
        ErasmusDocumentTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="case_documents",
    )
    amendment_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="amendments",
    )
    code = models.SlugField(max_length=64)
    title = models.CharField(max_length=160)
    document_type = models.CharField(
        max_length=32, choices=ErasmusDocumentRequirement.DocumentType.choices
    )
    lifecycle_phase = models.CharField(
        max_length=24, choices=ErasmusDocumentRequirement.Phase.choices
    )
    required = models.BooleanField(default=True)
    required_signer_roles = models.JSONField(default=list, blank=True)
    instructions = models.TextField(blank=True)
    external_system = models.CharField(max_length=32, blank=True)
    external_requirement_code = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.REQUESTED
    )
    revision_reason = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_tvg_erasmus_documents",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_erasmus_documents",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="returned_tvg_erasmus_documents",
    )
    returned_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("lifecycle_phase", "code", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("case", "requirement"),
                condition=models.Q(amendment_of__isnull=True),
                name="tvg_erasmus_unique_case_document_requirement",
            )
        ]
        permissions = (
            ("manage_case_documents", "Manage Erasmus case documents"),
            ("review_case_documents", "Review Erasmus case documents"),
            ("approve_case_documents", "Approve Erasmus case documents"),
            ("sign_case_documents", "Record authorized Erasmus signatures"),
            ("return_case_documents", "Return signed Erasmus documents"),
            ("download_case_documents", "Download Erasmus case documents"),
        )

    def __str__(self):
        return f"{self.case} - {self.title}"


class ErasmusDocumentVersion(HorillaModel):
    class Source(models.TextChoices):
        CANDIDATE_UPLOAD = "candidate_upload", _("Candidate upload")
        STAFF_UPLOAD = "staff_upload", _("Staff upload")
        TEMPLATE_COPY = "template_copy", _("Generated from approved template")
        MANUAL_SIGNED_UPLOAD = "manual_signed_upload", _("Manual signed upload")
        SIGNING_SERVICE = "signing_service", _("Signing service")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        ErasmusCaseDocument,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    file = models.FileField(
        upload_to=erasmus_document_version_path,
        storage=ErasmusDocumentPrivateStorage(),
        max_length=500,
    )
    original_filename = models.CharField(max_length=180)
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    source = models.CharField(max_length=32, choices=Source.choices)
    uploaded_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_tvg_erasmus_document_versions",
    )
    uploaded_by_person_public_id = models.UUIDField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "document__case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("document_id", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "version_number"),
                name="tvg_erasmus_unique_case_document_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Document versions are immutable."))
        return super().save(*args, **kwargs)


class ErasmusDocumentSignature(HorillaModel):
    class Method(models.TextChoices):
        MANUAL = "manual", _("Manual")
        SIGNING_SERVICE = "signing_service", _("Signing service")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        ErasmusCaseDocument, on_delete=models.PROTECT, related_name="signatures"
    )
    version = models.ForeignKey(
        ErasmusDocumentVersion, on_delete=models.PROTECT, related_name="signatures"
    )
    signer_role = models.CharField(
        max_length=40, choices=ErasmusDocumentRequirement.SignerRole.choices
    )
    signed_at = models.DateTimeField()
    method = models.CharField(max_length=24, choices=Method.choices)
    recorded_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="recorded_tvg_erasmus_signatures",
    )

    objects = HorillaCompanyManager(
        "document__case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("document", "version", "signer_role"),
                name="tvg_erasmus_unique_document_version_signature_role",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Signature records are immutable."))
        return super().save(*args, **kwargs)


class ErasmusSigningRequest(HorillaModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        ErasmusCaseDocument,
        on_delete=models.PROTECT,
        related_name="signing_requests",
    )
    version = models.ForeignKey(
        ErasmusDocumentVersion,
        on_delete=models.PROTECT,
        related_name="signing_requests",
    )
    provider_key = models.SlugField(max_length=50)
    provider_reference = models.CharField(max_length=180, blank=True)
    idempotency_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED
    )
    requested_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="requested_tvg_erasmus_signing",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "document__case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()


class ErasmusDocumentEvent(HorillaModel):
    class ActorType(models.TextChoices):
        STAFF = "staff", _("Staff")
        CANDIDATE = "candidate", _("Candidate")
        SIGNING_SERVICE = "signing_service", _("Signing service")
        SYSTEM = "system", _("System")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        ErasmusCaseDocument, on_delete=models.PROTECT, related_name="events"
    )
    event_type = models.CharField(max_length=64)
    actor_type = models.CharField(max_length=24, choices=ActorType.choices)
    actor_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_erasmus_document_events",
    )
    actor_person_public_id = models.UUIDField(null=True, blank=True)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "document__case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Document audit events are immutable."))
        return super().save(*args, **kwargs)


class ErasmusAccommodationRequest(HorillaModel):
    """Minimal follow-up signal; intentionally stores no free-text personal detail."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending HR follow-up")
        CONTACTED = "contacted", _("Contacted")
        CLOSED = "closed", _("Closed")

    case = models.OneToOneField(
        ErasmusCase,
        on_delete=models.PROTECT,
        related_name="accommodation_request",
    )
    needs_assistance = models.BooleanField()
    requested_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, blank=True)
    handled_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handled_tvg_accommodation_requests",
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "case__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        self.status = self.status or (
            self.Status.PENDING if self.needs_assistance else self.Status.CLOSED
        )
        return super().save(*args, **kwargs)
