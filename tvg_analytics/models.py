from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel

from .definitions import SCHEMA_VERSION


class AnalyticsSnapshot(HorillaModel):
    class Status(models.TextChoices):
        BUILDING = "building", _("Building")
        VALIDATED = "validated", _("Validated")
        FAILED = "failed", _("Validation failed")
        EXPORTED = "exported", _("Exported")
        SUPERSEDED = "superseded", _("Superseded")

    # A snapshot only moves forward; a rebuild creates a new revision instead.
    ALLOWED_TRANSITIONS = {
        Status.BUILDING: {Status.VALIDATED, Status.FAILED},
        Status.VALIDATED: {Status.EXPORTED, Status.SUPERSEDED},
        Status.EXPORTED: {Status.SUPERSEDED},
        Status.FAILED: set(),
        Status.SUPERSEDED: set(),
    }
    FROZEN_FIELDS = (
        "public_id",
        "company_id",
        "period_start",
        "period_end",
        "as_of_date",
        "filters",
        "fingerprint",
        "revision",
        "schema_version",
        "generated_by_id",
    )

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="tvg_analytics_snapshots"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    as_of_date = models.DateField()
    filters = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    schema_version = models.PositiveSmallIntegerField(
        default=SCHEMA_VERSION, editable=False
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.BUILDING
    )
    generated_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="generated_tvg_analytics_snapshots",
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    source_counts = models.JSONField(default=dict, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager("company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-generated_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("fingerprint", "revision"),
                name="tvg_analytics_unique_snapshot_revision",
            )
        ]
        permissions = (
            ("view_peopleops_analytics", "View PeopleOps analytics"),
            ("generate_analytics_snapshot", "Generate analytics snapshots"),
            ("export_analytics_snapshot", "Export validated analytics snapshots"),
        )

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.period_start
            and self.period_end
            and self.period_end < self.period_start
        ):
            errors["period_end"] = _("Period end cannot precede period start.")
        if self.as_of_date and self.period_end and self.as_of_date < self.period_end:
            errors["as_of_date"] = _("As-of date cannot precede period end.")
        if self.generated_by_id and self.generated_by.get_company() != self.company:
            errors["generated_by"] = _("Generator must belong to the same company.")
        if errors:
            raise ValidationError(errors)

    def _check_transition(self):
        stored = (
            type(self)
            .all_objects.filter(pk=self.pk)
            .values("status", *self.FROZEN_FIELDS)
            .first()
        )
        if stored is None:
            return
        changed = [
            field
            for field in self.FROZEN_FIELDS
            if getattr(self, field) != stored[field]
        ]
        if changed:
            raise ValidationError(
                _("Analytics snapshot fields are frozen: %(fields)s")
                % {"fields": ", ".join(changed)}
            )
        if self.status != stored["status"] and self.status not in (
            self.ALLOWED_TRANSITIONS.get(stored["status"], set())
        ):
            raise ValidationError(
                _("Analytics snapshot cannot move from %(old)s to %(new)s.")
                % {"old": stored["status"], "new": self.status}
            )

    def save(self, *args, **kwargs):
        if self.pk:
            self._check_transition()
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_exportable(self):
        return self.status == self.Status.VALIDATED


class AnalyticsMetricPoint(HorillaModel):
    class Dashboard(models.TextChoices):
        EXECUTIVE = "executive", _("Executive")
        RECRUITMENT = "recruitment", _("Recruitment")
        INTERNSHIP = "internship", _("Internship")
        ADMINISTRATION = "administration", _("Administration")

    snapshot = models.ForeignKey(
        AnalyticsSnapshot, on_delete=models.PROTECT, related_name="metric_points"
    )
    dashboard = models.CharField(max_length=24, choices=Dashboard.choices)
    metric_key = models.SlugField(max_length=100)
    period_start = models.DateField()
    period_end = models.DateField()
    value = models.DecimalField(max_digits=18, decimal_places=4)
    numerator = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    denominator = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    dimensions = models.JSONField(default=dict, blank=True)
    dimension_key = models.CharField(max_length=64, editable=False)
    # Set at build time; the Metabase reporting view withholds these values.
    is_small_cell = models.BooleanField(default=False, editable=False)

    objects = HorillaCompanyManager("snapshot__company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("dashboard", "metric_key", "dimension_key")
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "metric_key", "dimension_key"),
                name="tvg_analytics_unique_snapshot_metric_dimension",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Analytics metric points are immutable."))
        return super().save(*args, **kwargs)


class AnalyticsValidationResult(HorillaModel):
    class Outcome(models.TextChoices):
        PASSED = "passed", _("Passed")
        FAILED = "failed", _("Failed")

    snapshot = models.ForeignKey(
        AnalyticsSnapshot, on_delete=models.PROTECT, related_name="validation_results"
    )
    check_code = models.SlugField(max_length=100)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    expected_value = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    actual_value = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    detail = models.CharField(max_length=500, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)

    objects = HorillaCompanyManager("snapshot__company")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "check_code"),
                name="tvg_analytics_unique_snapshot_validation",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Analytics validation records are immutable."))
        return super().save(*args, **kwargs)


class AnalyticsExportAttempt(HorillaModel):
    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    snapshot = models.ForeignKey(
        AnalyticsSnapshot, on_delete=models.PROTECT, related_name="export_attempts"
    )
    sequence = models.PositiveIntegerField()
    destination = models.SlugField(max_length=40)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    idempotency_key = models.CharField(max_length=180, unique=True)
    external_reference = models.CharField(max_length=180, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    attempted_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_analytics_export_attempts",
    )
    attempted_at = models.DateTimeField(auto_now_add=True)

    objects = HorillaCompanyManager("snapshot__company")
    all_objects = models.Manager()

    class Meta:
        ordering = ("snapshot", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "sequence"),
                name="tvg_analytics_unique_export_sequence",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Analytics export attempts are immutable."))
        return super().save(*args, **kwargs)
