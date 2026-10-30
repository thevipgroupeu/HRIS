from django.contrib import admin

from tvg_onboarding.models import (
    AccountProvisioningRequest,
    AssetAcknowledgmentTemplate,
    InternOnboarding,
    ManualITTask,
    OnboardingAccessGrant,
    OnboardingAccessProfile,
    OnboardingAssetAllocation,
    OnboardingChecklistLink,
    OnboardingEvent,
    OnboardingPolicy,
    ScheduledOnboardingCommunication,
    StaffIdSequence,
)


@admin.register(InternOnboarding)
class InternOnboardingAdmin(admin.ModelAdmin):
    list_display = ("intern_id", "employee_badge_id", "person", "status", "start_date")
    list_filter = ("status", "venture", "department")
    search_fields = ("intern_id", "employee_badge_id", "person__primary_email")
    readonly_fields = (
        "public_id",
        "intern_id",
        "employee_badge_id",
        "approved_at",
        "onboarded_at",
    )


admin.site.register(OnboardingPolicy)
admin.site.register(StaffIdSequence)
admin.site.register(OnboardingChecklistLink)
admin.site.register(ScheduledOnboardingCommunication)
admin.site.register(AccountProvisioningRequest)
admin.site.register(ManualITTask)
admin.site.register(OnboardingAccessProfile)
admin.site.register(OnboardingAccessGrant)
admin.site.register(AssetAcknowledgmentTemplate)
admin.site.register(OnboardingAssetAllocation)
admin.site.register(OnboardingEvent)
