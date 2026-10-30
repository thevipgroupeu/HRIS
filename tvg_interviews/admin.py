from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from tvg_interviews.forms import DecisionTemplateAdminForm, InterviewTypeAdminForm
from tvg_interviews.models import DecisionNotificationTemplate, InterviewType


@admin.register(InterviewType)
class InterviewTypeAdmin(admin.ModelAdmin):
    form = InterviewTypeAdminForm
    list_display = (
        "name",
        "recruitment",
        "location_mode",
        "duration_minutes",
        "provider_key",
        "is_active",
    )
    list_filter = ("location_mode", "provider_key", "is_active")
    filter_horizontal = ("default_interviewers",)

    def has_add_permission(self, request):
        return request.user.has_perm("tvg_interviews.configure_interview_type")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("tvg_interviews.configure_interview_type")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("tvg_interviews.configure_interview_type")


@admin.register(DecisionNotificationTemplate)
class DecisionTemplateAdmin(admin.ModelAdmin):
    form = DecisionTemplateAdminForm
    list_display = (
        "name",
        "recruitment",
        "outcome",
        "approved",
        "approved_by",
        "approved_at",
    )
    list_filter = ("outcome", "approved")

    def has_add_permission(self, request):
        return request.user.has_perm("tvg_interviews.approve_decision_template")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("tvg_interviews.approve_decision_template")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("tvg_interviews.approve_decision_template")

    def save_model(self, request, obj, form, change):
        previous = DecisionNotificationTemplate.all_objects.filter(pk=obj.pk).first()
        approval_changed = obj.approved and (not previous or not previous.approved)
        if approval_changed:
            if not request.user.has_perm("tvg_interviews.approve_decision_template"):
                raise PermissionDenied
            employee = getattr(request.user, "employee_get", None)
            if not employee:
                raise PermissionDenied
            obj.approved_by = employee
            obj.approved_at = timezone.now()
        super().save_model(request, obj, form, change)
