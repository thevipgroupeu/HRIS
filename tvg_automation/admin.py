from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import PermissionDenied, ValidationError

from .models import (
    AutomationMessageTemplate,
    DeliveryAttempt,
    EventReplay,
    LifecycleEvent,
    WebhookReceipt,
    WorkflowExecution,
)
from .services import approve_template, cancel_event, replay_event


class LifecycleEventActionForm(ActionForm):
    operator_note = forms.CharField(
        required=False,
        max_length=500,
        label="Required audit note for cancel/replay",
    )


@admin.register(AutomationMessageTemplate)
class AutomationMessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("workflow_key", "version", "company", "is_active", "approved_at")
    list_filter = ("workflow_key", "is_active", "company")
    readonly_fields = ("approved_by", "approved_at")
    actions = ("approve_selected",)

    @admin.action(description="Approve and activate selected template versions")
    def approve_selected(self, request, queryset):
        approved = 0
        for template in queryset:
            try:
                approve_template(user=request.user, template=template)
                approved += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        if approved:
            self.message_user(request, f"Approved {approved} template version(s).")


@admin.register(LifecycleEvent)
class LifecycleEventAdmin(admin.ModelAdmin):
    action_form = LifecycleEventActionForm
    list_display = (
        "event_type",
        "event_id",
        "company",
        "status",
        "attempt_count",
        "occurred_at",
    )
    list_filter = ("event_type", "status", "company")
    search_fields = ("event_id", "idempotency_key", "aggregate_id")
    readonly_fields = tuple(field.name for field in LifecycleEvent._meta.fields)
    date_hierarchy = "occurred_at"
    actions = ("replay_failed", "cancel_pending")

    @admin.action(description="Replay selected failed events")
    def replay_failed(self, request, queryset):
        note = request.POST.get("operator_note", "").strip()
        if not note:
            self.message_user(
                request, "An audit note is required for replay.", level=messages.ERROR
            )
            return
        replayed = 0
        for event in queryset:
            try:
                replay_event(user=request.user, event=event, reason=note)
                replayed += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        if replayed:
            self.message_user(request, f"Queued {replayed} failed event(s) for replay.")

    @admin.action(description="Cancel selected undelivered events")
    def cancel_pending(self, request, queryset):
        note = request.POST.get("operator_note", "").strip()
        if not note:
            self.message_user(
                request,
                "An audit note is required for cancellation.",
                level=messages.ERROR,
            )
            return
        cancelled = 0
        for event in queryset:
            try:
                cancel_event(user=request.user, event=event, reason=note)
                cancelled += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        if cancelled:
            self.message_user(request, f"Cancelled {cancelled} event(s).")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAutomationHistoryAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(DeliveryAttempt, ReadOnlyAutomationHistoryAdmin)
admin.site.register(WorkflowExecution, ReadOnlyAutomationHistoryAdmin)
admin.site.register(WebhookReceipt, ReadOnlyAutomationHistoryAdmin)
admin.site.register(EventReplay, ReadOnlyAutomationHistoryAdmin)
