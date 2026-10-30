from __future__ import annotations

import uuid
from datetime import time

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel
from project.models import Project, Task

from .storage import ReportingPrivateStorage


def presentation_upload_path(instance, filename):
    presentation = getattr(instance, "presentation", instance)
    return f"presentations/{presentation.public_id}/{uuid.uuid4().hex}/{filename}"


class ReportingPolicy(HorillaModel):
    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, related_name="tvg_reporting_policy"
    )
    weekly_due_weekday = models.PositiveSmallIntegerField(
        default=4,
        validators=(MinValueValidator(0), MaxValueValidator(6)),
        help_text=_("0 is Monday and 6 is Sunday."),
    )
    weekly_due_time = models.TimeField(default=time(17, 0))
    weekly_reminder_hours_before = models.PositiveSmallIntegerField(default=24)
    monthly_due_day = models.PositiveSmallIntegerField(
        default=5, validators=(MinValueValidator(1), MaxValueValidator(28))
    )
    monthly_reminder_days_before = models.PositiveSmallIntegerField(default=3)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        permissions = (
            ("configure_reporting_policy", "Configure internship reporting policy"),
        )

    def __str__(self):
        return f"Reporting policy — {self.company}"


class ReportingRubricVersion(HorillaModel):
    class Dimension(models.TextChoices):
        WRITING = "report_writing", _("Report-writing quality")
        PERFORMANCE = "internship_performance", _("Internship performance")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    version_number = models.PositiveIntegerField()
    criteria = models.JSONField(
        default=list,
        help_text=_("List of code, label, dimension, weight and max_score objects."),
    )
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_reporting_rubrics",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("company", "name", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "name", "version_number"),
                name="tvg_reporting_unique_rubric_version",
            )
        ]
        permissions = (
            ("approve_reporting_rubric", "Approve reporting rubric versions"),
        )

    def clean(self):
        super().clean()
        errors = {}
        criteria = self.criteria if isinstance(self.criteria, list) else []
        seen = set()
        total_weight = 0
        if not criteria:
            errors["criteria"] = _("At least one rubric criterion is required.")
        for item in criteria:
            if not isinstance(item, dict):
                errors["criteria"] = _("Every criterion must be an object.")
                break
            required = {"code", "label", "dimension", "weight", "max_score"}
            if not required.issubset(item):
                errors["criteria"] = _("A rubric criterion is incomplete.")
                break
            code = str(item["code"]).strip()
            if not code or code in seen:
                errors["criteria"] = _("Rubric criterion codes must be unique.")
                break
            seen.add(code)
            if item["dimension"] not in self.Dimension.values:
                errors["criteria"] = _("Rubric criteria require a valid dimension.")
                break
            try:
                weight = int(item["weight"])
                max_score = int(item["max_score"])
            except (TypeError, ValueError):
                errors["criteria"] = _("Rubric weights and scores must be integers.")
                break
            if weight <= 0 or max_score <= 0:
                errors["criteria"] = _("Rubric weights and scores must be positive.")
                break
            total_weight += weight
        if criteria and "criteria" not in errors and total_weight != 100:
            errors["criteria"] = _("Rubric criterion weights must total 100.")
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
                    "name",
                    "version_number",
                    "criteria",
                    "approved_by_id",
                    "approved_at",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected
                ):
                    raise ValidationError(
                        _("Approved rubrics are immutable; create a new version.")
                    )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version_number}"


class ReportStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    REVISION_REQUIRED = "revision_required", _("Revision required")
    APPROVED = "approved", _("Approved")


class WeeklyReport(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="weekly_intern_reports"
    )
    reporting_week = models.DateField(help_text=_("Monday starting the report week."))
    assigned_project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="weekly_intern_reports"
    )
    planned_objectives = models.TextField()
    completed_tasks = models.TextField()
    completed_task_records = models.ManyToManyField(
        Task, blank=True, related_name="weekly_intern_reports"
    )
    evidence_links = models.JSONField(default=list, blank=True)
    blockers = models.TextField(blank=True)
    learning_outcomes = models.TextField()
    next_week_plan = models.TextField()
    hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    progress_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    deadline_at = models.DateTimeField()
    status = models.CharField(
        max_length=24, choices=ReportStatus.choices, default=ReportStatus.DRAFT
    )
    current_version = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-reporting_week", "employee")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "reporting_week"),
                name="tvg_reporting_unique_employee_week",
            )
        ]
        permissions = (
            ("review_weekly_report", "Review weekly internship reports"),
            ("view_management_reporting", "View management reporting summary"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.reporting_week and self.reporting_week.weekday() != 0:
            errors["reporting_week"] = _("The reporting week must start on Monday.")
        if self.employee_id and self.assigned_project_id:
            if self.employee.get_company() != self.assigned_project.company_id:
                errors["assigned_project"] = _(
                    "Project and employee must belong to the same company."
                )
        if self.hours is not None and self.hours < 0:
            errors["hours"] = _("Hours cannot be negative.")
        if not isinstance(self.evidence_links, list) or any(
            not isinstance(value, str) for value in self.evidence_links
        ):
            errors["evidence_links"] = _("Evidence links must be a list of URLs.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.status == ReportStatus.APPROVED:
                raise ValidationError(_("Approved reports are immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — week of {self.reporting_week}"


class WeeklyReportVersion(HorillaModel):
    report = models.ForeignKey(
        WeeklyReport, on_delete=models.PROTECT, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    content = models.JSONField()
    submitted_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="submitted_weekly_versions"
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("report__employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("report", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("report", "version_number"),
                name="tvg_reporting_unique_weekly_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Submitted report versions are immutable."))
        return super().save(*args, **kwargs)


class MonthlyPresentation(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="monthly_intern_presentations",
    )
    presentation_month = models.DateField(help_text=_("Use the first day of month."))
    assigned_project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="monthly_intern_presentations"
    )
    presentation_file = models.FileField(
        storage=ReportingPrivateStorage(),
        upload_to=presentation_upload_path,
        blank=True,
    )
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    presentation_date = models.DateField()
    project_milestones = models.TextField()
    deadline_at = models.DateTimeField()
    status = models.CharField(
        max_length=24, choices=ReportStatus.choices, default=ReportStatus.DRAFT
    )
    current_version = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-presentation_month", "employee")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "presentation_month"),
                name="tvg_reporting_unique_employee_month",
            )
        ]
        permissions = (
            (
                "review_monthly_presentation",
                "Review monthly internship presentations",
            ),
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.presentation_month and self.presentation_month.day != 1:
            errors["presentation_month"] = _(
                "Presentation month must use the first day of the month."
            )
        if self.employee_id and self.assigned_project_id:
            if self.employee.get_company() != self.assigned_project.company_id:
                errors["assigned_project"] = _(
                    "Project and employee must belong to the same company."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            if original and original.status == ReportStatus.APPROVED:
                raise ValidationError(_("Approved presentations are immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.presentation_month:%Y-%m}"


class MonthlyPresentationVersion(HorillaModel):
    presentation = models.ForeignKey(
        MonthlyPresentation, on_delete=models.PROTECT, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    presentation_file = models.FileField(
        storage=ReportingPrivateStorage(), upload_to=presentation_upload_path
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    content = models.JSONField()
    submitted_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="submitted_presentation_versions",
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "presentation__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("presentation", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("presentation", "version_number"),
                name="tvg_reporting_unique_presentation_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Submitted presentation versions are immutable."))
        return super().save(*args, **kwargs)


class ReviewOutcome(models.TextChoices):
    APPROVED = "approved", _("Approved")
    REVISION_REQUIRED = "revision_required", _("Revision required")


class WeeklyReportReview(HorillaModel):
    version = models.OneToOneField(
        WeeklyReportVersion, on_delete=models.PROTECT, related_name="review"
    )
    reviewer = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="weekly_report_reviews"
    )
    outcome = models.CharField(max_length=24, choices=ReviewOutcome.choices)
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

    objects = HorillaCompanyManager(
        "version__report__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Review history is immutable."))
        return super().save(*args, **kwargs)


class MonthlyPresentationReview(HorillaModel):
    version = models.OneToOneField(
        MonthlyPresentationVersion, on_delete=models.PROTECT, related_name="review"
    )
    reviewer = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="presentation_reviews"
    )
    outcome = models.CharField(max_length=24, choices=ReviewOutcome.choices)
    comments = models.TextField()
    milestone_evidence = models.TextField(blank=True)
    development_feedback = models.TextField(blank=True)
    writing_quality_comments = models.TextField(blank=True)
    internship_performance_comments = models.TextField(blank=True)
    rubric = models.ForeignKey(ReportingRubricVersion, on_delete=models.PROTECT)
    rubric_scores = models.JSONField(default=dict)
    reviewer_adjustments = models.JSONField(default=dict, blank=True)
    adjustment_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "version__presentation__employee__employee_work_info__company_id"
    )
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Review history is immutable."))
        return super().save(*args, **kwargs)


class ReportingAIAnalysis(HorillaModel):
    class Purpose(models.TextChoices):
        SUMMARY = "summary", _("Report summary")
        MISSING_EVIDENCE = "missing_evidence", _("Missing-evidence highlights")
        SUPERVISOR_BRIEFING = "supervisor_briefing", _("Supervisor briefing")

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        COMPLETED = "completed", _("Completed")
        UNAVAILABLE = "unavailable", _("Unavailable")
        FAILED = "failed", _("Failed")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    weekly_version = models.ForeignKey(
        WeeklyReportVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ai_analyses",
    )
    presentation_version = models.ForeignKey(
        MonthlyPresentationVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ai_analyses",
    )
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    provider = models.CharField(max_length=80)
    model_version = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices)
    result = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="requested_reporting_ai"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()

    def clean(self):
        super().clean()
        targets = int(bool(self.weekly_version_id)) + int(
            bool(self.presentation_version_id)
        )
        if targets != 1:
            raise ValidationError(
                _("AI analysis must reference exactly one submitted version.")
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.status in {
                self.Status.COMPLETED,
                self.Status.UNAVAILABLE,
                self.Status.FAILED,
            }:
                raise ValidationError(_("Completed AI analysis records are immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)


class AIReviewerAdjustment(HorillaModel):
    analysis = models.ForeignKey(
        ReportingAIAnalysis, on_delete=models.PROTECT, related_name="adjustments"
    )
    reviewer = models.ForeignKey(Employee, on_delete=models.PROTECT)
    adjusted_result = models.JSONField(default=dict)
    reason = models.TextField()
    adjusted_at = models.DateTimeField(default=timezone.now)

    objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(_("AI reviewer adjustments are immutable."))
        return super().save(*args, **kwargs)


class ReportingNotification(HorillaModel):
    class Kind(models.TextChoices):
        WEEKLY_REMINDER = "weekly_reminder", _("Weekly report reminder")
        WEEKLY_OVERDUE = "weekly_overdue", _("Weekly report overdue")
        MONTHLY_REMINDER = "monthly_reminder", _("Monthly presentation reminder")
        REVIEW_REQUIRED = "review_required", _("Supervisor review required")
        REVIEW_COMPLETED = "review_completed", _("Supervisor review completed")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        DELIVERED = "delivered", _("Delivered")
        CANCELED = "canceled", _("Canceled")

    dedupe_key = models.CharField(max_length=180, unique=True)
    recipient = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="reporting_notifications"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    weekly_report = models.ForeignKey(
        WeeklyReport, on_delete=models.PROTECT, null=True, blank=True
    )
    presentation = models.ForeignKey(
        MonthlyPresentation, on_delete=models.PROTECT, null=True, blank=True
    )
    scheduled_for = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    native_notification_id = models.PositiveBigIntegerField(null=True, blank=True)

    objects = HorillaCompanyManager("recipient__employee_work_info__company_id")
    all_objects = models.Manager()

    def clean(self):
        super().clean()
        targets = int(bool(self.weekly_report_id)) + int(bool(self.presentation_id))
        if targets != 1:
            raise ValidationError(
                _("A reporting notification must reference exactly one item.")
            )


class ReportingEvent(HorillaModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    actor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="performed_reporting_events",
    )
    event_type = models.CharField(max_length=80)
    object_type = models.CharField(max_length=40)
    object_public_id = models.UUIDField()
    version_number = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("employee__employee_work_info__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Reporting audit events are immutable."))
        return super().save(*args, **kwargs)
