from __future__ import annotations

import uuid
from string import Formatter

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel


class AutomationMessageTemplate(HorillaModel):
    """Editable until approved; approved versions are immutable."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_automation_message_templates",
    )
    workflow_key = models.SlugField(max_length=80)
    version = models.PositiveIntegerField()
    subject_template = models.CharField(max_length=255)
    body_template = models.TextField()
    from_email = models.EmailField(default="recruitment@thevipgroup.eu")
    reply_to = models.EmailField(default="recruitment@thevipgroup.eu")
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_automation_message_templates",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("workflow_key", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "workflow_key", "version"),
                name="tvg_automation_unique_template_version",
            )
        ]
        permissions = [
            ("approve_message_template", "Can approve automation message template"),
        ]

    @property
    def is_approved(self):
        return bool(self.approved_by_id and self.approved_at)

    def clean(self):
        from .rendering import allowed_placeholders

        allowed = allowed_placeholders(self.workflow_key)
        for value in (self.subject_template, self.body_template):
            try:
                fields = {
                    field_name
                    for _literal, field_name, _format, _conversion in Formatter().parse(
                        value
                    )
                    if field_name
                }
            except ValueError as exc:
                raise ValidationError("Template placeholders are malformed.") from exc
            unknown = fields - allowed
            if unknown:
                raise ValidationError(
                    f"Unsupported template placeholder(s): {', '.join(sorted(unknown))}."
                )
        if self.from_email.casefold() != "recruitment@thevipgroup.eu":
            raise ValidationError(
                {
                    "from_email": "Recruitment automation must use recruitment@thevipgroup.eu."
                }
            )
        if bool(self.approved_by_id) != bool(self.approved_at):
            raise ValidationError(
                "Template approval requires both actor and timestamp."
            )

    def save(self, *args, **kwargs):
        original = None
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
        if original and original.is_approved:
            immutable = (
                "company_id",
                "workflow_key",
                "version",
                "subject_template",
                "body_template",
                "from_email",
                "reply_to",
                "approved_by_id",
                "approved_at",
            )
            if any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError("Approved template versions are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.workflow_key} v{self.version}"


class LifecycleEvent(HorillaModel):
    class Status(models.TextChoices):
        AWAITING_APPROVAL = "awaiting_approval", _("Awaiting human approval")
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        RETRY = "retry", _("Retry scheduled")
        DELIVERED = "delivered", _("Delivered")
        FAILED = "failed", _("Failed / review required")
        CANCELLED = "cancelled", _("Cancelled")

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_lifecycle_events",
    )
    event_type = models.CharField(max_length=100)
    schema_version = models.PositiveSmallIntegerField(default=1, editable=False)
    workflow_key = models.SlugField(max_length=80)
    aggregate_type = models.SlugField(max_length=50)
    aggregate_id = models.UUIDField()
    source_model = models.CharField(max_length=100)
    source_id = models.UUIDField()
    idempotency_key = models.CharField(max_length=180, unique=True)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    human_approval_required = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_lifecycle_events",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING
    )
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=60, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    cancelled_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_lifecycle_events",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)
    replay_count = models.PositiveSmallIntegerField(default=0)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at", "pk")
        indexes = [
            models.Index(fields=("status", "next_attempt_at"), name="tvg_auto_due_idx"),
            models.Index(
                fields=("company", "status"), name="tvg_auto_company_status_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("source_model", "source_id"),
                name="tvg_automation_unique_source_event",
            )
        ]
        permissions = [
            ("authorize_lifecycle_event", "Can authorize lifecycle event"),
            ("cancel_lifecycle_event", "Can cancel lifecycle event"),
            ("replay_lifecycle_event", "Can replay failed lifecycle event"),
            ("review_automation_failure", "Can review automation failure"),
        ]

    def clean(self):
        if not isinstance(self.payload, dict):
            raise ValidationError({"payload": "Lifecycle payload must be an object."})
        if self.schema_version != 1:
            raise ValidationError({"schema_version": "Unsupported schema version."})
        if self.human_approval_required and bool(self.approved_by_id) != bool(
            self.approved_at
        ):
            raise ValidationError(
                "Approval evidence requires both actor and timestamp."
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            immutable = (
                "event_id",
                "company_id",
                "event_type",
                "schema_version",
                "workflow_key",
                "aggregate_type",
                "aggregate_id",
                "source_model",
                "source_id",
                "idempotency_key",
                "payload",
                "occurred_at",
                "human_approval_required",
            )
            if original and any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError("Lifecycle event facts are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"


class DeliveryAttempt(HorillaModel):
    class Outcome(models.TextChoices):
        DELIVERED = "delivered", _("Delivered")
        RETRY = "retry", _("Retry")
        FAILED = "failed", _("Failed")

    event = models.ForeignKey(
        LifecycleEvent,
        on_delete=models.PROTECT,
        related_name="delivery_attempts",
    )
    sequence = models.PositiveIntegerField()
    environment = models.SlugField(max_length=32)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(default=timezone.now)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    external_execution_id = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager("event__company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("event", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "sequence"),
                name="tvg_automation_unique_delivery_sequence",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Delivery attempts are immutable.")
        return super().save(*args, **kwargs)


class WorkflowExecution(HorillaModel):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        RUNNING = "running", _("Running")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    event = models.ForeignKey(
        LifecycleEvent,
        on_delete=models.PROTECT,
        related_name="workflow_executions",
    )
    workflow_key = models.SlugField(max_length=80)
    external_execution_id = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=Status.choices)
    started_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status_message = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager("event__company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("event", "external_execution_id"),
                name="tvg_automation_unique_external_execution",
            )
        ]


class WebhookReceipt(HorillaModel):
    idempotency_key = models.CharField(max_length=180, unique=True)
    event = models.ForeignKey(
        LifecycleEvent,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="webhook_receipts",
    )
    request_timestamp = models.DateTimeField()
    body_sha256 = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()

    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Webhook receipts are immutable.")
        return super().save(*args, **kwargs)


class EventReplay(HorillaModel):
    event = models.ForeignKey(
        LifecycleEvent,
        on_delete=models.PROTECT,
        related_name="replays",
    )
    requested_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="automation_event_replays",
    )
    reason = models.CharField(max_length=500)
    previous_error_code = models.CharField(max_length=60, blank=True)
    previous_error = models.CharField(max_length=500, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager("event__company")
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Event replay records are immutable.")
        return super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Replay audit records are immutable.")
        return super().save(*args, **kwargs)
