from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department, JobPosition
from employee.models import Employee
from helpdesk.models import Ticket, TicketType
from horilla.models import HorillaModel
from project.models import Task
from tvg_core.models import Venture


class EmployeeVentureAssignment(HorillaModel):
    """Explicit venture link for staff who do not have an intern onboarding."""

    employee = models.OneToOneField(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_venture_assignment",
    )
    venture = models.ForeignKey(
        Venture,
        on_delete=models.PROTECT,
        related_name="peoplehub_employee_assignments",
    )
    assigned_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="assigned_peoplehub_ventures",
    )
    assigned_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        permissions = (("assign_employee_venture", "Assign an employee venture"),)

    def clean(self):
        super().clean()
        errors = {}
        employee_company = self.employee.get_company() if self.employee_id else None
        if self.venture_id and employee_company != self.venture.legal_employer:
            errors["venture"] = _("Venture and employee must share a legal employer.")
        if self.assigned_by_id and self.assigned_by.get_company() != employee_company:
            errors["assigned_by"] = _(
                "Assigner and employee must share a legal employer."
            )
        if self.employee_id and hasattr(self.employee, "tvg_intern_onboarding"):
            onboarding = self.employee.tvg_intern_onboarding
            if onboarding.venture_id != self.venture_id:
                errors["venture"] = _(
                    "Intern venture is owned by the onboarding record."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.venture}"


class PeopleHubRequestType(HorillaModel):
    class Category(models.TextChoices):
        HR = "hr", _("HR question")
        IT = "it", _("IT support")
        EQUIPMENT = "equipment", _("Equipment access")
        SOFTWARE = "software", _("Software access")
        DOCUMENT = "document", _("Document request")
        ACCOMMODATION = "accommodation", _("Accommodation support")
        OTHER = "other", _("Other approved request")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    code = models.SlugField(max_length=64)
    title = models.CharField(max_length=100)
    category = models.CharField(max_length=24, choices=Category.choices)
    native_ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        related_name="peoplehub_request_types",
    )
    target_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True
    )
    target_job_position = models.ForeignKey(
        JobPosition, on_delete=models.PROTECT, null=True, blank=True
    )
    instructions = models.CharField(max_length=255, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_peoplehub_request_types",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("category", "title")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "code"),
                name="tvg_peoplehub_unique_request_type_code",
            )
        ]
        permissions = (("approve_request_type", "Approve PeopleHub request types"),)

    def clean(self):
        super().clean()
        errors = {}
        targets = int(bool(self.target_department_id)) + int(
            bool(self.target_job_position_id)
        )
        if targets != 1:
            errors["target_department"] = _(
                "Choose exactly one approved department or job-position target."
            )
        if (
            self.native_ticket_type_id
            and self.native_ticket_type.company_id_id != self.company_id
        ):
            errors["native_ticket_type"] = _(
                "Native ticket type must belong to the same company."
            )
        if (
            self.target_department_id
            and not self.target_department.company_id.filter(
                pk=self.company_id
            ).exists()
        ):
            errors["target_department"] = _(
                "Target department must belong to the same company."
            )
        if (
            self.target_job_position_id
            and not self.target_job_position.company_id.filter(
                pk=self.company_id
            ).exists()
        ):
            errors["target_job_position"] = _(
                "Target job position must belong to the same company."
            )
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
            if original and original.approved_at:
                protected = (
                    "company_id",
                    "code",
                    "title",
                    "category",
                    "native_ticket_type_id",
                    "target_department_id",
                    "target_job_position_id",
                    "instructions",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _("Approved request types are immutable; create a new type.")
                    )
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def assigning_type(self):
        return "department" if self.target_department_id else "job_position"

    @property
    def raised_on_id(self):
        return self.target_department_id or self.target_job_position_id

    def __str__(self):
        return self.title


class PeopleHubLearningAssignment(HorillaModel):
    class Kind(models.TextChoices):
        TRAINING = "training", _("Training programme")
        MOODLE = "moodle", _("Moodle course")
        SKILL = "skill", _("Skill development")
        WEEKLY_REPORT = "weekly_report", _("Weekly reporting")
        MONTHLY_PRESENTATION = "monthly_presentation", _("Monthly presentation")

    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="peoplehub_learning_assignments",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=160)
    native_task = models.ForeignKey(
        Task,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="peoplehub_learning_assignments",
    )
    provider = models.CharField(max_length=40, default="internal")
    external_reference = models.CharField(max_length=180, blank=True)
    launch_url = models.URLField(blank=True)
    progress_percent = models.PositiveSmallIntegerField(
        default=0,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ASSIGNED
    )
    due_date = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="assigned_peoplehub_learning",
    )

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("due_date", "title")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "kind", "title"),
                name="tvg_peoplehub_unique_learning_assignment",
            )
        ]
        permissions = (
            ("manage_learning_assignments", "Manage PeopleHub learning assignments"),
        )

    def clean(self):
        super().clean()
        if self.employee_id and self.assigned_by_id:
            if self.employee.get_company() != self.assigned_by.get_company():
                raise ValidationError(
                    {"assigned_by": _("Assigner and employee must share a company.")}
                )
        if self.native_task_id and self.employee_id:
            if not self.native_task.task_members.filter(pk=self.employee_id).exists():
                raise ValidationError(
                    {"native_task": _("Employee must be a member of the native task.")}
                )
        if self.provider == "moodle" and not self.external_reference:
            raise ValidationError(
                {
                    "external_reference": _(
                        "Moodle assignments require a course reference."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"


class PeopleHubRequestLink(HorillaModel):
    request_type = models.ForeignKey(
        PeopleHubRequestType, on_delete=models.PROTECT, related_name="request_links"
    )
    native_ticket = models.OneToOneField(
        Ticket, on_delete=models.PROTECT, related_name="peoplehub_request_link"
    )
    submitted_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    submitted_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("request_type__company")
    all_objects = models.Manager()

    def clean(self):
        super().clean()
        errors = {}
        if self.submitted_by_id and self.request_type_id:
            if self.submitted_by.get_company() != self.request_type.company:
                errors["submitted_by"] = _(
                    "Submitter and request type must share a company."
                )
        if self.native_ticket_id and self.submitted_by_id:
            if self.native_ticket.employee_id_id != self.submitted_by_id:
                errors["native_ticket"] = _(
                    "Native ticket owner must be the PeopleHub submitter."
                )
        if self.native_ticket_id and self.request_type_id:
            if (
                self.native_ticket.ticket_type_id
                != self.request_type.native_ticket_type_id
            ):
                errors["native_ticket"] = _(
                    "Native ticket type must match the approved request route."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PeopleHubEvent(HorillaModel):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="peoplehub_events"
    )
    event_type = models.CharField(max_length=80)
    object_public_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("PeopleHub audit events are immutable."))
        return super().save(*args, **kwargs)
