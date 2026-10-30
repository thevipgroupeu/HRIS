from django.contrib import admin

from tvg_erasmus.document_services import (
    approve_document_requirement,
    approve_document_template,
)
from tvg_erasmus.forms import (
    AcceptanceTemplateAdminForm,
    ErasmusDocumentTemplateAdminForm,
)
from tvg_erasmus.models import (
    AcceptanceLetter,
    AcceptanceLetterTemplate,
    AcceptanceNotification,
    DurationException,
    ErasmusAccommodationRequest,
    ErasmusArrangementPolicy,
    ErasmusCase,
    ErasmusCaseDocument,
    ErasmusCaseEvent,
    ErasmusDocumentEvent,
    ErasmusDocumentRequirement,
    ErasmusDocumentSignature,
    ErasmusDocumentTemplate,
    ErasmusDocumentVersion,
    ErasmusSigningRequest,
    ScreeningDecision,
    VerificationAuditEvent,
    VerificationCase,
    VerificationChecklist,
    VerificationEvidence,
    VerificationNotification,
    VerificationRequirement,
    VerificationRequirementTemplate,
)


class VerificationRequirementTemplateInline(admin.TabularInline):
    model = VerificationRequirementTemplate
    extra = 0

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")


@admin.register(VerificationChecklist)
class VerificationChecklistAdmin(admin.ModelAdmin):
    list_display = ("title", "recruitment", "due_days", "retention_days", "is_active")
    search_fields = ("title", "recruitment__title")
    inlines = (VerificationRequirementTemplateInline,)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")

    def has_add_permission(self, request):
        return request.user.has_perm("tvg_erasmus.configure_verification")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_verification")


@admin.register(ScreeningDecision)
class ScreeningDecisionAdmin(admin.ModelAdmin):
    list_display = ("application", "status", "decided_by", "decided_at")
    readonly_fields = (
        "application",
        "status",
        "applicant_message",
        "internal_note",
        "decided_by",
        "decided_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class VerificationReadOnlyAdmin(admin.ModelAdmin):
    """Sensitive workflow records are changed only through audited services."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(VerificationCase, VerificationReadOnlyAdmin)
admin.site.register(VerificationRequirement, VerificationReadOnlyAdmin)
admin.site.register(VerificationEvidence, VerificationReadOnlyAdmin)
admin.site.register(VerificationAuditEvent, VerificationReadOnlyAdmin)
admin.site.register(VerificationNotification, VerificationReadOnlyAdmin)


@admin.register(ErasmusArrangementPolicy)
class ErasmusArrangementPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "minimum_duration_months",
        "letter_number_prefix",
        "next_letter_sequence",
    )

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_arrangement_policy")

    def has_add_permission(self, request):
        return request.user.has_perm("tvg_erasmus.configure_arrangement_policy")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("tvg_erasmus.configure_arrangement_policy")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AcceptanceLetterTemplate)
class AcceptanceLetterTemplateAdmin(admin.ModelAdmin):
    form = AcceptanceTemplateAdminForm
    list_display = (
        "name",
        "version_number",
        "company",
        "approved_by",
        "approved_at",
        "is_active",
    )
    search_fields = ("name", "legal_entity_name")
    readonly_fields = ("approved_by", "approved_at")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.approved_at:
            return tuple(field.name for field in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(ErasmusCase, VerificationReadOnlyAdmin)
admin.site.register(DurationException, VerificationReadOnlyAdmin)
admin.site.register(AcceptanceLetter, VerificationReadOnlyAdmin)
admin.site.register(ErasmusCaseEvent, VerificationReadOnlyAdmin)
admin.site.register(AcceptanceNotification, VerificationReadOnlyAdmin)


@admin.register(ErasmusDocumentTemplate)
class ErasmusDocumentTemplateAdmin(admin.ModelAdmin):
    form = ErasmusDocumentTemplateAdminForm
    list_display = (
        "name",
        "version_number",
        "document_type",
        "company",
        "approved_by",
        "approved_at",
    )
    readonly_fields = (
        "content_type",
        "sha256",
        "size_bytes",
        "approved_by",
        "approved_at",
    )
    actions = ("approve_selected",)

    @admin.action(description="Approve and lock selected template versions")
    def approve_selected(self, request, queryset):
        approved = 0
        for template in queryset.filter(approved_at__isnull=True):
            approve_document_template(user=request.user, template=template)
            approved += 1
        self.message_user(request, f"Approved {approved} template version(s).")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.approved_at:
            return tuple(field.name for field in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ErasmusDocumentRequirement)
class ErasmusDocumentRequirementAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "version_number",
        "title",
        "document_type",
        "lifecycle_phase",
        "university",
        "required",
        "approved_at",
    )
    readonly_fields = ("approved_by", "approved_at")
    actions = ("approve_selected",)

    @admin.action(description="Approve and lock selected document requirements")
    def approve_selected(self, request, queryset):
        approved = 0
        for requirement in queryset.filter(approved_at__isnull=True):
            approve_document_requirement(user=request.user, requirement=requirement)
            approved += 1
        self.message_user(request, f"Approved {approved} requirement(s).")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.approved_at:
            return tuple(field.name for field in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(ErasmusCaseDocument, VerificationReadOnlyAdmin)
admin.site.register(ErasmusDocumentVersion, VerificationReadOnlyAdmin)
admin.site.register(ErasmusDocumentSignature, VerificationReadOnlyAdmin)
admin.site.register(ErasmusSigningRequest, VerificationReadOnlyAdmin)
admin.site.register(ErasmusDocumentEvent, VerificationReadOnlyAdmin)
admin.site.register(ErasmusAccommodationRequest, VerificationReadOnlyAdmin)
