from django.contrib import admin

from .models import (
    AnalyticsExportAttempt,
    AnalyticsMetricPoint,
    AnalyticsSnapshot,
    AnalyticsValidationResult,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Analytics records are produced and audited by services only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AnalyticsSnapshot)
class AnalyticsSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "public_id",
        "company",
        "period_start",
        "period_end",
        "revision",
        "status",
    )
    list_filter = ("status", "company")


@admin.register(AnalyticsMetricPoint)
class AnalyticsMetricPointAdmin(ReadOnlyAdmin):
    list_display = ("snapshot", "dashboard", "metric_key", "value", "is_small_cell")
    list_filter = ("dashboard",)


@admin.register(AnalyticsValidationResult)
class AnalyticsValidationResultAdmin(ReadOnlyAdmin):
    list_display = ("snapshot", "check_code", "outcome", "checked_at")
    list_filter = ("outcome",)


@admin.register(AnalyticsExportAttempt)
class AnalyticsExportAttemptAdmin(ReadOnlyAdmin):
    list_display = ("snapshot", "sequence", "destination", "outcome", "attempted_at")
    list_filter = ("outcome", "destination")
