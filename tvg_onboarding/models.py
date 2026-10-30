from __future__ import annotations

import uuid
from string import Formatter

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asset.models import AssetAssignment
from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department
from employee.models import Employee
from horilla.models import HorillaModel
from onboarding.models import CandidateStage, CandidateTask, OnboardingStage
from tvg_core.models import Person, Venture
from tvg_erasmus.models import ErasmusCase
from tvg_onboarding.storage import OnboardingPrivateStorage


def acknowledgment_path(instance, filename):
    return (
        f"asset-acknowledgments/{instance.onboarding.public_id}/"
        f"v{instance.document_version}/{filename}"
    )


class OnboardingPolicy(HorillaModel):
    class Provider(models.TextChoices):
        MANUAL = "manual", _("Manual IT task")
        ZOHO = "zoho", _("Zoho")
        SELF_HOSTED = "self_hosted", _("Self-hosted identity/mail")

    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, related_name="tvg_onboarding_policy"
    )
    intern_id_prefix = models.CharField(max_length=20, default="TVG-INT")
    employee_id_prefix = models.CharField(max_length=20, default="TVG-EMP")
    welcome_email_lead_days = models.PositiveSmallIntegerField(default=7)
    identity_provider = models.CharField(
        max_length=24, choices=Provider.choices, default=Provider.MANUAL
    )

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        permissions = (("configure_onboarding_policy", "Configure onboarding policy"),)

    def clean(self):
        super().clean()
        errors = {}
        for field in ("intern_id_prefix", "employee_id_prefix"):
            value = (getattr(self, field) or "").strip().upper()
            setattr(self, field, value)
            if not value or any(
                char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for char in value
            ):
                errors[field] = _("Use uppercase letters, numbers and hyphens only.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class StaffIdSequence(HorillaModel):
    class Kind(models.TextChoices):
        INTERN = "intern", _("Intern")
        EMPLOYEE = "employee", _("Employee")

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    next_number = models.PositiveIntegerField(default=1)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "year", "kind"),
                name="tvg_onboarding_unique_staff_id_sequence",
            )
        ]


class InternOnboarding(HorillaModel):
    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", _("Pending approval")
        APPROVED = "approved", _("Approved")
        IN_PROGRESS = "in_progress", _("In progress")
        ONBOARDED = "onboarded", _("Onboarded")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    erasmus_case = models.OneToOneField(
        ErasmusCase, on_delete=models.PROTECT, related_name="intern_onboarding"
    )
    person = models.ForeignKey(
        Person, on_delete=models.PROTECT, related_name="onboardings"
    )
    employee = models.OneToOneField(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_intern_onboarding",
    )
    native_stage = models.ForeignKey(
        OnboardingStage, on_delete=models.PROTECT, related_name="tvg_intern_onboardings"
    )
    native_candidate_stage = models.OneToOneField(
        CandidateStage,
        on_delete=models.PROTECT,
        related_name="tvg_intern_onboarding",
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING_APPROVAL
    )
    intern_id = models.CharField(max_length=64, unique=True)
    employee_badge_id = models.CharField(max_length=64, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    venture = models.ForeignKey(Venture, on_delete=models.PROTECT)
    supervisor = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="supervised_tvg_onboardings"
    )
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_tvg_onboardings",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    onboarded_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_tvg_onboardings",
    )
    onboarded_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        permissions = (
            ("start_intern_onboarding", "Start intern onboarding"),
            ("approve_intern_onboarding", "Approve intern onboarding"),
            ("manage_intern_onboarding", "Manage intern onboarding"),
            ("complete_intern_onboarding", "Complete intern onboarding"),
            ("manage_onboarding_access", "Manage onboarding access"),
            ("manage_account_provisioning", "Manage account provisioning"),
            ("allocate_onboarding_asset", "Allocate onboarding assets"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.erasmus_case_id:
            case = self.erasmus_case
            application = case.application
            if self.person_id != application.person_id:
                errors["person"] = _("Person must match the selected application.")
            if self.venture_id != application.opening.venture_id:
                errors["venture"] = _("Venture must match the selected application.")
            if (
                self.department_id != case.department_id
                or self.supervisor_id != case.supervisor_id
            ):
                errors["department"] = _(
                    "Department and supervisor must match approved arrangements."
                )
            if (
                self.start_date != case.confirmed_start_date
                or self.end_date != case.confirmed_end_date
            ):
                errors["start_date"] = _("Dates must match approved arrangements.")
        if (
            self.employee_id
            and self.person_id
            and self.person.employee_id not in (None, self.employee_id)
        ):
            errors["employee"] = _("Person identity is linked to another employee.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "erasmus_case_id",
                "person_id",
                "intern_id",
                "employee_badge_id",
                "start_date",
                "end_date",
                "department_id",
                "venture_id",
                "supervisor_id",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(
                    _(
                        "Approved onboarding identity and arrangement fields are immutable."
                    )
                )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.intern_id} - {self.person}"


class OnboardingChecklistLink(HorillaModel):
    onboarding = models.ForeignKey(
        InternOnboarding, on_delete=models.PROTECT, related_name="checklist_items"
    )
    code = models.CharField(max_length=64)
    native_task = models.OneToOneField(
        CandidateTask, on_delete=models.PROTECT, related_name="tvg_checklist_link"
    )
    due_date = models.DateField(null=True, blank=True)
    required = models.BooleanField(default=True)
    completed_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, null=True, blank=True
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    evidence_note = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("onboarding", "code"),
                name="tvg_onboarding_unique_checklist_code",
            )
        ]


class ScheduledOnboardingCommunication(HorillaModel):
    class Kind(models.TextChoices):
        WELCOME = "welcome", _("Welcome email")
        ARRIVAL = "arrival", _("Arrival reminder and office instructions")

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        SENT = "sent", _("Sent")
        CANCELLED = "cancelled", _("Cancelled")

    onboarding = models.ForeignKey(
        InternOnboarding, on_delete=models.PROTECT, related_name="communications"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    scheduled_for = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SCHEDULED
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    external_reference = models.CharField(max_length=180, blank=True)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("onboarding", "kind"),
                name="tvg_onboarding_unique_scheduled_communication",
            )
        ]


class AccountProvisioningRequest(HorillaModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        MANUAL_REQUIRED = "manual_required", _("Manual action required")
        PROVISIONED = "provisioned", _("Provisioned")
        FAILED = "failed", _("Failed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    onboarding = models.OneToOneField(
        InternOnboarding, on_delete=models.PROTECT, related_name="provisioning_request"
    )
    provider = models.CharField(max_length=32)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.REQUESTED
    )
    requested_email = models.EmailField(blank=True)
    provisioned_email = models.EmailField(blank=True)
    external_reference = models.CharField(max_length=180, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requested_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="requested_tvg_accounts"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_tvg_accounts",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()


class ManualITTask(HorillaModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    provisioning_request = models.OneToOneField(
        AccountProvisioningRequest,
        on_delete=models.PROTECT,
        related_name="manual_task",
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
        "provisioning_request__onboarding__venture__legal_employer"
    )
    all_objects = models.Manager()


class OnboardingAccessProfile(HorillaModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    venture = models.ForeignKey(Venture, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    groups = models.ManyToManyField(
        Group, related_name="tvg_onboarding_access_profiles"
    )
    approved_group_ids = models.JSONField(default=list, blank=True)
    approved_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, null=True, blank=True
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "name"), name="tvg_onboarding_unique_access_profile"
            )
        ]
        permissions = (
            ("approve_onboarding_access_profile", "Approve onboarding access profiles"),
        )

    def clean(self):
        super().clean()
        if self.venture_id and self.company_id != self.venture.legal_employer_id:
            raise ValidationError(
                {"venture": _("Venture must belong to the profile company.")}
            )
        if (
            self.department_id
            and not self.department.company_id.filter(pk=self.company_id).exists()
        ):
            raise ValidationError(
                {"department": _("Department must belong to the profile company.")}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "name",
                    "venture_id",
                    "department_id",
                    "approved_group_ids",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _(
                            "Approved access profiles are immutable; create a new profile."
                        )
                    )
        self.full_clean()
        return super().save(*args, **kwargs)


class OnboardingAccessGrant(HorillaModel):
    onboarding = models.OneToOneField(
        InternOnboarding, on_delete=models.PROTECT, related_name="access_grant"
    )
    profile = models.ForeignKey(OnboardingAccessProfile, on_delete=models.PROTECT)
    group_snapshot = models.JSONField(default=list)
    granted_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    granted_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()


class AssetAcknowledgmentTemplate(HorillaModel):
    ALLOWED_PLACEHOLDERS = {
        "company_name",
        "intern_id",
        "employee_badge_id",
        "employee_name",
        "asset_name",
        "asset_serial_number",
        "allocation_date",
    }

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    version_number = models.PositiveIntegerField(default=1)
    body = models.TextField()
    approved_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, null=True, blank=True
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "name", "version_number"),
                name="tvg_onboarding_unique_asset_template_version",
            )
        ]
        permissions = (
            ("approve_asset_ack_template", "Approve asset acknowledgment templates"),
        )

    def clean(self):
        super().clean()
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
            raise ValidationError(
                {"body": _("Template contains an unsupported placeholder.")}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "name",
                    "version_number",
                    "body",
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


class OnboardingAssetAllocation(HorillaModel):
    class AcknowledgmentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PREPARED = "prepared", _("Prepared")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    onboarding = models.ForeignKey(
        InternOnboarding, on_delete=models.PROTECT, related_name="asset_allocations"
    )
    native_assignment = models.OneToOneField(
        AssetAssignment,
        on_delete=models.PROTECT,
        related_name="tvg_onboarding_allocation",
    )
    template = models.ForeignKey(
        AssetAcknowledgmentTemplate, on_delete=models.PROTECT, null=True, blank=True
    )
    document_version = models.PositiveIntegerField(default=0)
    rendered_text = models.TextField(blank=True)
    pdf_file = models.FileField(
        upload_to=acknowledgment_path,
        storage=OnboardingPrivateStorage(),
        max_length=500,
        blank=True,
    )
    sha256 = models.CharField(max_length=64, blank=True)
    acknowledgment_status = models.CharField(
        max_length=16,
        choices=AcknowledgmentStatus.choices,
        default=AcknowledgmentStatus.PENDING,
    )
    prepared_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="prepared_tvg_asset_acknowledgments",
    )
    prepared_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledgment_reference = models.CharField(max_length=180, blank=True)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()


class OnboardingEvent(HorillaModel):
    onboarding = models.ForeignKey(
        InternOnboarding, on_delete=models.PROTECT, related_name="events"
    )
    event_type = models.CharField(max_length=80)
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("onboarding__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Onboarding audit events are immutable."))
        return super().save(*args, **kwargs)
