from __future__ import annotations

import uuid
from datetime import timedelta
from string import Formatter

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel
from offboarding.models import Offboarding, OffboardingEmployee
from tvg_onboarding.models import InternOnboarding
from tvg_reporting.models import ReportingRubricVersion

from .storage import CompletionPrivateStorage


def default_access_systems():
    return [
        {"key": "company_email", "label": "Company email", "provider": "manual"},
        {"key": "github", "label": "GitHub", "provider": "manual"},
        {"key": "vpn", "label": "VPN", "provider": "manual"},
        {"key": "sso", "label": "SSO", "provider": "manual"},
        {"key": "application", "label": "Application access", "provider": "manual"},
    ]


def completion_document_path(instance, filename):
    return f"documents/{instance.case.public_id}/{instance.public_id}/{filename}"


def revocation_proof_path(instance, filename):
    return f"revocation/{instance.case.public_id}/{instance.public_id}/{filename}"


class CompletionPolicy(HorillaModel):
    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, related_name="tvg_completion_policy"
    )
    native_offboarding = models.ForeignKey(
        Offboarding, on_delete=models.PROTECT, related_name="tvg_completion_policies"
    )
    start_days_before_end = models.PositiveSmallIntegerField(
        default=30, validators=(MinValueValidator(1),)
    )
    require_final_weekly_report = models.BooleanField(default=True)
    require_final_monthly_presentation = models.BooleanField(default=True)
    require_final_presentation_review = models.BooleanField(default=True)
    require_asset_return_document = models.BooleanField(default=True)
    access_systems = models.JSONField(default=default_access_systems)
    hr_recipients = models.ManyToManyField(
        Employee, blank=True, related_name="tvg_completion_hr_policies"
    )

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        permissions = (("configure_completion_policy", "Configure completion policy"),)

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.native_offboarding_id
            and self.native_offboarding.company_id_id != self.company_id
        ):
            errors["native_offboarding"] = _(
                "The native offboarding process must belong to this company."
            )
        rows = self.access_systems if isinstance(self.access_systems, list) else []
        keys = []
        allowed = {"company_email", "github", "vpn", "sso", "application"}
        for row in rows:
            if not isinstance(row, dict) or not {"key", "label", "provider"}.issubset(
                row
            ):
                errors["access_systems"] = _(
                    "Every access system requires key, label and provider values."
                )
                break
            key = str(row["key"]).strip()
            if key not in allowed:
                errors["access_systems"] = _(
                    "An unsupported access system was configured."
                )
                break
            keys.append(key)
        if len(keys) != len(set(keys)):
            errors["access_systems"] = _("Access system keys must be unique.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class CompletionCase(HorillaModel):
    class State(models.TextChoices):
        UPCOMING = "upcoming", _("Upcoming")
        IN_PROGRESS = "in_progress", _("In Progress")
        AWAITING_DOCUMENTS = "awaiting_documents", _("Awaiting Documents")
        AWAITING_ASSETS = "awaiting_assets", _("Awaiting Assets")
        AWAITING_ACCESS_REVOCATION = "awaiting_access_revocation", _(
            "Awaiting Access Revocation"
        )
        COMPLETED = "completed", _("Completed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    onboarding = models.OneToOneField(
        InternOnboarding, on_delete=models.PROTECT, related_name="completion_case"
    )
    employee = models.OneToOneField(
        Employee, on_delete=models.PROTECT, related_name="tvg_completion_case"
    )
    native_offboarding_employee = models.OneToOneField(
        OffboardingEmployee,
        on_delete=models.PROTECT,
        related_name="tvg_completion_case",
    )
    state = models.CharField(
        max_length=32, choices=State.choices, default=State.UPCOMING
    )
    internship_end_date = models.DateField()
    policy_snapshot = models.JSONField(default=dict)
    final_presentation_scheduled_for = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="started_tvg_completions",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_tvg_completions",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("internship_end_date", "employee")
        permissions = (
            ("start_intern_completion", "Start internship completion"),
            ("manage_intern_completion", "Manage internship completion"),
            (
                "authorize_completion_document",
                "Authorize completion document signatures",
            ),
            ("complete_intern_completion", "Complete internship completion"),
            ("approve_completion_exception", "Approve completion exceptions"),
            ("manage_access_revocation", "Manage access revocation"),
            ("manage_completion_assets", "Manage completion asset returns"),
            ("view_completion_management", "View completion management dashboard"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.onboarding_id:
            if self.employee_id != self.onboarding.employee_id:
                errors["employee"] = _("Employee must match the onboarding record.")
            if self.internship_end_date != self.onboarding.end_date:
                errors["internship_end_date"] = _(
                    "Completion end date must match approved onboarding dates."
                )
        if self.native_offboarding_employee_id and self.employee_id:
            if self.native_offboarding_employee.employee_id_id != self.employee_id:
                errors["native_offboarding_employee"] = _(
                    "Native offboarding employee must reference the same employee."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.get_state_display()}"


class CompletionRequirement(HorillaModel):
    class Code(models.TextChoices):
        FINAL_WEEKLY_REPORT = "final_weekly_report", _("Final weekly report")
        FINAL_MONTHLY_PRESENTATION = "final_monthly_presentation", _(
            "Final monthly presentation"
        )
        FINAL_PRESENTATION_REVIEW = "final_presentation_review", _(
            "Final presentation review"
        )
        FINAL_SUPERVISOR_FEEDBACK = "final_supervisor_feedback", _(
            "Final supervisor feedback"
        )
        COMPLETION_CERTIFICATE = "completion_certificate", _(
            "Internship completion certificate"
        )
        UNIVERSITY_DOCUMENTS = "university_documents", _(
            "University After Mobility documents"
        )
        ASSET_RETURN = "asset_return", _("Company assets returned")
        ASSET_RETURN_DOCUMENT = "asset_return_document", _(
            "Signed asset return document"
        )
        ACCESS_REVOCATION = "access_revocation", _("Access revoked")
        ADMINISTRATIVE_CLOSURE = "administrative_closure", _(
            "Final administrative closure"
        )

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        COMPLETED = "completed", _("Completed")
        EXCEPTED = "excepted", _("Authorized exception")

    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="requirements"
    )
    code = models.CharField(max_length=40, choices=Code.choices)
    title = models.CharField(max_length=180)
    required = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    source_type = models.CharField(max_length=48, blank=True)
    source_reference = models.CharField(max_length=180, blank=True)
    completed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_tvg_completion_requirements",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("case", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("case", "code"), name="tvg_completion_unique_requirement_code"
            )
        ]


class CompletionException(HorillaModel):
    requirement = models.OneToOneField(
        CompletionRequirement,
        on_delete=models.PROTECT,
        related_name="authorized_exception",
    )
    reason = models.TextField()
    approved_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "requirement__case__onboarding__venture__legal_employer"
    )
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Completion exceptions are immutable."))
        return super().save(*args, **kwargs)


class FinalSupervisorFeedback(HorillaModel):
    case = models.OneToOneField(
        CompletionCase, on_delete=models.PROTECT, related_name="final_feedback"
    )
    reviewer = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="final_intern_feedback"
    )
    comments = models.TextField()
    task_completion_evidence = models.TextField(blank=True)
    development_feedback = models.TextField(blank=True)
    writing_quality_comments = models.TextField(blank=True)
    internship_performance_comments = models.TextField(blank=True)
    rubric = models.ForeignKey(ReportingRubricVersion, on_delete=models.PROTECT)
    rubric_scores = models.JSONField(default=dict)
    reviewer_adjustments = models.JSONField(default=dict, blank=True)
    adjustment_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Final supervisor feedback is immutable."))
        return super().save(*args, **kwargs)


class CompletionDocumentTemplate(HorillaModel):
    class Kind(models.TextChoices):
        COMPLETION_CERTIFICATE = "completion_certificate", _("Completion certificate")
        ASSET_RETURN = "asset_return", _("Asset handover/return")
        REFERENCE_LETTER = "reference_letter", _("Reference letter")

    class SignerRole(models.TextChoices):
        COMPANY_AUTHORIZED = "company_authorized", _(
            "Authorized company representative"
        )
        INTERN = "intern", _("Intern")
        SUPERVISOR = "supervisor", _("Supervisor")

    ALLOWED_PLACEHOLDERS = {
        "company_name",
        "intern_id",
        "employee_name",
        "internship_start_date",
        "internship_end_date",
        "university",
        "programme",
        "department",
        "supervisor_name",
        "issue_date",
        "asset_summary",
    }

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    name = models.CharField(max_length=140)
    version_number = models.PositiveIntegerField(default=1)
    body = models.TextField()
    required_signer_roles = models.JSONField(default=list)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_completion_templates",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "kind", "name", "version_number"),
                name="tvg_completion_unique_template_version",
            )
        ]
        permissions = (("approve_completion_template", "Approve completion templates"),)

    def clean(self):
        super().clean()
        errors = {}
        try:
            placeholders = {
                name
                for _literal, name, _spec, _conversion in Formatter().parse(self.body)
                if name
            }
        except ValueError as exc:
            raise ValidationError(
                {"body": _("Template placeholders are malformed.")}
            ) from exc
        if placeholders - self.ALLOWED_PLACEHOLDERS:
            errors["body"] = _("Template contains an unsupported placeholder.")
        roles = (
            self.required_signer_roles
            if isinstance(self.required_signer_roles, list)
            else []
        )
        allowed_roles = set(self.SignerRole.values)
        if (
            not roles
            or len(roles) != len(set(roles))
            or any(role not in allowed_roles for role in roles)
        ):
            errors["required_signer_roles"] = _(
                "Templates require a unique list of supported human signer roles."
            )
        if (
            self.kind == self.Kind.COMPLETION_CERTIFICATE
            and self.SignerRole.COMPANY_AUTHORIZED not in roles
        ):
            errors["required_signer_roles"] = _(
                "Completion certificates require an authorized company representative."
            )
        if self.kind == self.Kind.ASSET_RETURN and not {
            self.SignerRole.COMPANY_AUTHORIZED,
            self.SignerRole.INTERN,
        }.issubset(set(roles)):
            errors["required_signer_roles"] = _(
                "Asset return documents require company and intern signatures."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "kind",
                    "name",
                    "version_number",
                    "body",
                    "required_signer_roles",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _("Approved templates are immutable; create a new version.")
                    )
        self.full_clean()
        return super().save(*args, **kwargs)


class CompletionDocument(HorillaModel):
    class Status(models.TextChoices):
        PREPARED = "prepared", _("Prepared")
        SIGNED = "signed", _("Signed")
        ISSUED = "issued", _("Issued")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="documents"
    )
    kind = models.CharField(
        max_length=32, choices=CompletionDocumentTemplate.Kind.choices
    )
    template = models.ForeignKey(CompletionDocumentTemplate, on_delete=models.PROTECT)
    version_number = models.PositiveIntegerField()
    issue_date = models.DateField()
    rendered_text = models.TextField()
    pdf_file = models.FileField(
        storage=CompletionPrivateStorage(),
        upload_to=completion_document_path,
        max_length=500,
    )
    sha256 = models.CharField(max_length=64)
    required_signer_roles = models.JSONField(default=list)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PREPARED
    )
    prepared_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="prepared_completion_documents"
    )
    prepared_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="issued_completion_documents",
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("case", "kind", "version_number"),
                name="tvg_completion_unique_document_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            protected = (
                "case_id",
                "kind",
                "template_id",
                "version_number",
                "issue_date",
                "rendered_text",
                "pdf_file",
                "sha256",
                "required_signer_roles",
                "prepared_by_id",
                "prepared_at",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in protected
            ):
                raise ValidationError(_("Prepared document versions are immutable."))
        return super().save(*args, **kwargs)


class CompletionDocumentSignature(HorillaModel):
    document = models.ForeignKey(
        CompletionDocument, on_delete=models.PROTECT, related_name="signatures"
    )
    signer_role = models.CharField(
        max_length=32, choices=CompletionDocumentTemplate.SignerRole.choices
    )
    signed_at = models.DateTimeField()
    recorded_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    evidence_reference = models.CharField(max_length=180, blank=True)

    objects = HorillaCompanyManager(
        "document__case__onboarding__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("document", "signer_role"),
                name="tvg_completion_unique_document_signer_role",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Signature records are immutable."))
        return super().save(*args, **kwargs)


class ReferenceLetterRequest(HorillaModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        APPROVED = "approved", _("Approved")
        DECLINED = "declined", _("Declined")
        PREPARED = "prepared", _("Prepared")
        DELIVERED = "delivered", _("Delivered")

    case = models.OneToOneField(
        CompletionCase, on_delete=models.PROTECT, related_name="reference_request"
    )
    requested_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="reference_letter_requests"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED
    )
    handled_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handled_reference_letter_requests",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    document = models.OneToOneField(
        CompletionDocument,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reference_request",
    )

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()


class AccessRevocationTask(HorillaModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        MANUAL_REQUIRED = "manual_required", _("Manual action required")
        REVOKED = "revoked", _("Revoked")
        FAILED = "failed", _("Failed")
        EXCEPTED = "excepted", _("Authorized exception")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="access_revocations"
    )
    system_key = models.SlugField(max_length=40)
    system_label = models.CharField(max_length=120)
    provider = models.CharField(max_length=40)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.REQUESTED
    )
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requested_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="requested_access_revocations",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    revoked_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_access_revocations",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    proof_reference = models.CharField(max_length=180, blank=True)
    proof_file = models.FileField(
        storage=CompletionPrivateStorage(),
        upload_to=revocation_proof_path,
        max_length=500,
        blank=True,
    )
    proof_sha256 = models.CharField(max_length=64, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("case", "system_key"),
                name="tvg_completion_unique_access_system",
            )
        ]


class ManualAccessRevocationTask(HorillaModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        COMPLETED = "completed", _("Completed")

    revocation = models.OneToOneField(
        AccessRevocationTask, on_delete=models.PROTECT, related_name="manual_task"
    )
    title = models.CharField(max_length=180)
    instructions = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )
    completed_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, null=True, blank=True
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "revocation__case__onboarding__venture__legal_employer"
    )
    all_objects = models.Manager()


class AccessRevocationException(HorillaModel):
    revocation = models.OneToOneField(
        AccessRevocationTask,
        on_delete=models.PROTECT,
        related_name="authorized_exception",
    )
    reason = models.TextField()
    approved_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "revocation__case__onboarding__venture__legal_employer"
    )
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Access-revocation exceptions are immutable."))
        return super().save(*args, **kwargs)


class RetentionPolicy(HorillaModel):
    class RecordKind(models.TextChoices):
        COMPLETION_CERTIFICATE = "completion_certificate", _("Completion certificate")
        ASSET_RETURN = "asset_return", _("Asset return document")
        REFERENCE_LETTER = "reference_letter", _("Reference letter")
        UNIVERSITY_DOCUMENT = "university_document", _("University document")
        ACCESS_PROOF = "access_proof", _("Access-revocation proof")

    class Action(models.TextChoices):
        REVIEW = "review", _("Review at retention date")
        RETAIN_INDEFINITELY = "retain_indefinitely", _("Retain indefinitely")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    record_kind = models.CharField(max_length=32, choices=RecordKind.choices)
    name = models.CharField(max_length=140)
    version_number = models.PositiveIntegerField(default=1)
    action = models.CharField(max_length=24, choices=Action.choices)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_completion_retention_policies",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "record_kind", "name", "version_number"),
                name="tvg_completion_unique_retention_policy_version",
            )
        ]
        permissions = (
            ("approve_retention_policy", "Approve completion retention policies"),
        )

    def clean(self):
        super().clean()
        if self.action == self.Action.REVIEW and not self.retention_days:
            raise ValidationError(
                {"retention_days": _("Review policies require retention days.")}
            )
        if self.action == self.Action.RETAIN_INDEFINITELY and self.retention_days:
            raise ValidationError(
                {
                    "retention_days": _(
                        "Indefinite retention cannot have a review interval."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "record_kind",
                    "name",
                    "version_number",
                    "action",
                    "retention_days",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _(
                            "Approved retention policies are immutable; create a new version."
                        )
                    )
        self.full_clean()
        return super().save(*args, **kwargs)


class RetentionSchedule(HorillaModel):
    class Status(models.TextChoices):
        RETAINED = "retained", _("Retained")
        REVIEW_DUE = "review_due", _("Review due")
        LEGAL_HOLD = "legal_hold", _("Legal hold")
        ARCHIVED = "archived", _("Archived")

    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="retention_schedules"
    )
    policy = models.ForeignKey(RetentionPolicy, on_delete=models.PROTECT)
    record_kind = models.CharField(
        max_length=32, choices=RetentionPolicy.RecordKind.choices
    )
    object_public_id = models.CharField(max_length=180)
    retain_until = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RETAINED
    )
    legal_hold_reason = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("case", "record_kind", "object_public_id"),
                name="tvg_completion_unique_retention_schedule",
            )
        ]


class CompletionNotification(HorillaModel):
    class Kind(models.TextChoices):
        UPCOMING_END = "upcoming_end", _("Upcoming internship end")
        FINAL_REPORTING = "final_reporting", _("Final reporting requested")
        FINAL_PRESENTATION = "final_presentation", _("Final presentation scheduled")
        REVIEW_REQUIRED = "review_required", _("Final review required")
        ASSETS_OUTSTANDING = "assets_outstanding", _("Assets outstanding")
        ACCESS_REVOCATION = "access_revocation", _("Access revocation required")
        COMPLETED = "completed", _("Completion finalized")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        DELIVERED = "delivered", _("Delivered")
        CANCELED = "canceled", _("Canceled")

    dedupe_key = models.CharField(max_length=200, unique=True)
    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="notifications"
    )
    recipient = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="completion_notifications"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    scheduled_for = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    native_notification_id = models.PositiveBigIntegerField(null=True, blank=True)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()


class CompletionEvent(HorillaModel):
    case = models.ForeignKey(
        CompletionCase, on_delete=models.PROTECT, related_name="events"
    )
    event_type = models.CharField(max_length=80)
    actor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completion_events_performed",
    )
    object_type = models.CharField(max_length=48, blank=True)
    object_reference = models.CharField(max_length=180, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("case__onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Completion audit events are immutable."))
        return super().save(*args, **kwargs)
