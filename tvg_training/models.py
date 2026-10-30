from __future__ import annotations

import uuid

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department
from employee.models import Employee
from horilla.models import HorillaModel
from tvg_core.models import Venture
from tvg_onboarding.models import InternOnboarding
from tvg_peoplehub.models import PeopleHubLearningAssignment


class TrainingProgramme(HorillaModel):
    class Category(models.TextChoices):
        HR_PEOPLE = "hr_people", _("HR and People Operations")
        FLUXOPS_TECH = "fluxops_tech", _("FluxOps engineering / AI / MLOps")
        FINDMYSTAY_PRODUCT = "findmystay_product", _("FindMyStay software and product")
        FINDMYSTAY_GROWTH = "findmystay_growth", _("FindMyStay marketing and growth")
        SALES = "sales", _("Sales and business development")
        COMPANY_ONBOARDING = "company_onboarding", _("Company policies and onboarding")
        IT_INFRASTRUCTURE = "it_infrastructure", _("IT and infrastructure")

    class Audience(models.TextChoices):
        ALL = "all", _("All staff")
        INTERN = "intern", _("Interns")
        EMPLOYEE = "employee", _("Employees")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tvg_training_programmes"
    )
    code = models.SlugField(max_length=64)
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=32, choices=Category.choices)
    audience = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.ALL
    )
    venture = models.ForeignKey(
        Venture,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="training_programmes",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_training_programmes",
    )
    default_deadline_days = models.PositiveSmallIntegerField(
        default=30, validators=(MinValueValidator(1), MaxValueValidator(730))
    )
    is_active = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_training_programmes",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("category", "title", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "code", "version"),
                name="tvg_training_unique_program_version",
            )
        ]
        permissions = (
            ("approve_training_programme", "Approve training programmes"),
            ("assign_training_programme", "Assign training programmes"),
            ("provision_training", "Provision and synchronize training"),
        )

    @property
    def is_approved(self):
        return bool(self.approved_by_id and self.approved_at)

    def clean(self):
        super().clean()
        errors = {}
        if self.venture_id and self.venture.legal_employer_id != self.company_id:
            errors["venture"] = _("Venture must belong to the programme company.")
        if (
            self.department_id
            and not self.department.company_id.filter(pk=self.company_id).exists()
        ):
            errors["department"] = _("Department must belong to the programme company.")
        if self.approved_by_id and self.approved_by.get_company() != self.company:
            errors["approved_by"] = _("Approver must belong to the same company.")
        if bool(self.approved_by_id) != bool(self.approved_at):
            errors["approved_by"] = _(
                "Approval requires both an authorized actor and timestamp."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.is_approved:
                immutable = (
                    "company_id",
                    "code",
                    "version",
                    "title",
                    "description",
                    "category",
                    "audience",
                    "venture_id",
                    "department_id",
                    "default_deadline_days",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in immutable
                ):
                    raise ValidationError(
                        _("Approved programmes are immutable; create a new version.")
                    )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} v{self.version}"


class TrainingCourse(HorillaModel):
    class Provider(models.TextChoices):
        MOODLE = "moodle", _("Moodle")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tvg_training_courses"
    )
    provider = models.CharField(
        max_length=32, choices=Provider.choices, default=Provider.MOODLE
    )
    external_course_id = models.CharField(max_length=180)
    title = models.CharField(max_length=160)
    launch_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("title",)
        constraints = [
            models.UniqueConstraint(
                fields=("company", "provider", "external_course_id"),
                name="tvg_training_unique_external_course",
            )
        ]

    def __str__(self):
        return self.title


class TrainingProgrammeCourse(HorillaModel):
    programme = models.ForeignKey(
        TrainingProgramme, on_delete=models.PROTECT, related_name="programme_courses"
    )
    course = models.ForeignKey(
        TrainingCourse, on_delete=models.PROTECT, related_name="programme_links"
    )
    sequence = models.PositiveSmallIntegerField(default=1)
    mandatory = models.BooleanField(default=True)
    deadline_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=(MinValueValidator(1), MaxValueValidator(730)),
    )

    objects = HorillaCompanyManager("programme__company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("sequence", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("programme", "course"),
                name="tvg_training_unique_program_course",
            )
        ]

    def clean(self):
        super().clean()
        if self.programme_id and self.course_id:
            if self.programme.company_id != self.course.company_id:
                raise ValidationError(
                    {"course": _("Course and programme must share a company.")}
                )

    def save(self, *args, **kwargs):
        original = (
            type(self).all_objects.filter(pk=self.pk).first() if self.pk else None
        )
        if self.programme_id and self.programme.is_approved:
            protected = (
                "programme_id",
                "course_id",
                "sequence",
                "mandatory",
                "deadline_days",
            )
            if original is None or any(
                getattr(original, field) != getattr(self, field) for field in protected
            ):
                raise ValidationError(
                    _("Courses in an approved programme version are immutable.")
                )
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.programme.is_approved:
            raise ValidationError(
                _("Courses in an approved programme version are immutable.")
            )
        return super().delete(*args, **kwargs)


class TrainingAssignment(HorillaModel):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        PROVISIONING = "provisioning", _("Provisioning")
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Review required")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="training_assignments"
    )
    programme = models.ForeignKey(
        TrainingProgramme,
        on_delete=models.PROTECT,
        related_name="employee_assignments",
    )
    onboarding = models.ForeignKey(
        InternOnboarding,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="training_assignments",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ASSIGNED
    )
    due_date = models.DateField()
    idempotency_key = models.CharField(max_length=180, unique=True)
    assigned_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="created_training_assignments",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    authorized_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="authorized_training_assignments",
    )
    authorized_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("due_date", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "programme"),
                name="tvg_training_unique_employee_program",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        employee_company = self.employee.get_company() if self.employee_id else None
        if self.programme_id and employee_company != self.programme.company:
            errors["programme"] = _("Programme and employee must share a company.")
        if self.assigned_by_id and self.assigned_by.get_company() != employee_company:
            errors["assigned_by"] = _("Assigner must belong to the same company.")
        if (
            self.authorized_by_id
            and self.authorized_by.get_company() != employee_company
        ):
            errors["authorized_by"] = _("Authorizer must belong to the same company.")
        if self.onboarding_id and self.onboarding.employee_id != self.employee_id:
            errors["onboarding"] = _("Onboarding must belong to the employee.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MoodleAccountLink(HorillaModel):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        DISABLED = "disabled", _("Disabled")

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="moodle_account_links"
    )
    employee = models.OneToOneField(
        Employee, on_delete=models.PROTECT, related_name="moodle_account_link"
    )
    external_user_id = models.CharField(max_length=180)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    linked_at = models.DateTimeField(default=timezone.now)
    last_synchronized_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "external_user_id"),
                name="tvg_training_unique_moodle_user",
            )
        ]

    def clean(self):
        super().clean()
        if self.employee_id and self.employee.get_company() != self.company:
            raise ValidationError(
                {"employee": _("Moodle account and employee must share a company.")}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MoodleEnrollment(HorillaModel):
    class Status(models.TextChoices):
        ENROLLED = "enrolled", _("Enrolled")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Review required")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    assignment = models.ForeignKey(
        TrainingAssignment, on_delete=models.PROTECT, related_name="enrollments"
    )
    programme_course = models.ForeignKey(
        TrainingProgrammeCourse,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    peoplehub_assignment = models.OneToOneField(
        PeopleHubLearningAssignment,
        on_delete=models.PROTECT,
        related_name="moodle_enrollment",
    )
    external_enrollment_id = models.CharField(max_length=180)
    idempotency_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ENROLLED
    )
    progress_percent = models.PositiveSmallIntegerField(
        default=0, validators=(MinValueValidator(0), MaxValueValidator(100))
    )
    due_date = models.DateField()
    completed_at = models.DateTimeField(null=True, blank=True)
    last_synchronized_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager(
        "assignment__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("due_date", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "programme_course"),
                name="tvg_training_unique_enrollment_course",
            )
        ]

    def clean(self):
        super().clean()
        if self.assignment_id and self.programme_course_id:
            if self.assignment.programme_id != self.programme_course.programme_id:
                raise ValidationError(
                    {"programme_course": _("Course is outside the assigned programme.")}
                )
        if self.peoplehub_assignment_id and self.assignment_id:
            if self.peoplehub_assignment.employee_id != self.assignment.employee_id:
                raise ValidationError(
                    {
                        "peoplehub_assignment": _(
                            "PeopleHub assignment belongs to another employee."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TrainingSyncAttempt(HorillaModel):
    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    assignment = models.ForeignKey(
        TrainingAssignment, on_delete=models.PROTECT, related_name="sync_attempts"
    )
    sequence = models.PositiveIntegerField()
    operation = models.CharField(max_length=32)
    idempotency_key = models.CharField(max_length=180)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    provider_reference = models.CharField(max_length=180, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "assignment__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("assignment", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "sequence"),
                name="tvg_training_unique_sync_sequence",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Training synchronization attempts are immutable."))
        return super().save(*args, **kwargs)


class TrainingNotification(HorillaModel):
    class Kind(models.TextChoices):
        OVERDUE = "overdue", _("Mandatory training overdue")

    enrollment = models.OneToOneField(
        MoodleEnrollment, on_delete=models.PROTECT, related_name="overdue_notification"
    )
    supervisor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="training_notifications",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OVERDUE)
    idempotency_key = models.CharField(max_length=180, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "enrollment__assignment__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()


class StaffIdentityLink(HorillaModel):
    class Provider(models.TextChoices):
        AUTHENTIK = "authentik", _("authentik")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        DISABLED = "disabled", _("Disabled")
        REVOKED = "revoked", _("Revoked")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="staff_identity_links"
    )
    employee = models.OneToOneField(
        Employee, on_delete=models.PROTECT, related_name="staff_identity_link"
    )
    provider = models.CharField(
        max_length=24, choices=Provider.choices, default=Provider.AUTHENTIK
    )
    subject = models.CharField(max_length=180)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    session_version = models.PositiveIntegerField(default=1)
    linked_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="linked_staff_identities",
    )
    linked_at = models.DateTimeField(default=timezone.now)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revoked_staff_identities",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "provider", "subject"),
                name="tvg_training_unique_staff_subject",
            )
        ]
        permissions = (
            ("manage_staff_identity", "Manage staff identity links"),
            ("approve_staff_role_mapping", "Approve staff SSO role mappings"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.employee_id and self.employee.get_company() != self.company:
            errors["employee"] = _("Identity and employee must share a company.")
        if self.linked_by_id and self.linked_by.get_company() != self.company:
            errors["linked_by"] = _("Linking actor must belong to the same company.")
        if self.revoked_by_id and self.revoked_by.get_company() != self.company:
            errors["revoked_by"] = _("Revoking actor must belong to the same company.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class StaffRoleMapping(HorillaModel):
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="staff_role_mappings"
    )
    provider = models.CharField(
        max_length=24,
        choices=StaffIdentityLink.Provider.choices,
        default=StaffIdentityLink.Provider.AUTHENTIK,
    )
    external_role = models.CharField(max_length=120)
    group = models.ForeignKey(
        Group, on_delete=models.PROTECT, related_name="staff_sso_role_mappings"
    )
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_staff_role_mappings",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("company", "provider", "external_role"),
                name="tvg_training_unique_external_role",
            )
        ]

    @property
    def is_approved(self):
        return bool(self.approved_by_id and self.approved_at)

    def clean(self):
        super().clean()
        if self.approved_by_id and self.approved_by.get_company() != self.company:
            raise ValidationError(
                {"approved_by": _("Approver must belong to the same company.")}
            )
        if bool(self.approved_by_id) != bool(self.approved_at):
            raise ValidationError(
                {"approved_by": _("Approval requires actor and timestamp.")}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.is_approved:
                immutable = (
                    "company_id",
                    "provider",
                    "external_role",
                    "group_id",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in immutable
                ):
                    raise ValidationError(
                        _("Approved SSO role mappings are immutable.")
                    )
        self.full_clean()
        return super().save(*args, **kwargs)


class StaffIdentitySession(HorillaModel):
    identity = models.ForeignKey(
        StaffIdentityLink, on_delete=models.PROTECT, related_name="sessions"
    )
    session_key_hash = models.CharField(max_length=64, unique=True)
    provider_session_id_hash = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("identity__company")
    all_objects = models.Manager()


class TrainingEvent(HorillaModel):
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="training_events"
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="training_events",
    )
    event_type = models.CharField(max_length=80)
    object_public_id = models.UUIDField(null=True, blank=True)
    actor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="authored_training_events",
    )
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at", "pk")

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Training audit events are immutable."))
        return super().save(*args, **kwargs)
