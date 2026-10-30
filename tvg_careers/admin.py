from django.contrib import admin
from django.core.exceptions import PermissionDenied

from tvg_careers.forms import ApplicationAdminForm, JobOpeningAdminForm
from tvg_careers.models import (
    Application,
    ApplicationConfirmationEvent,
    JobOpening,
    RecruitmentQuestionnaireVersion,
)
from tvg_careers.services import set_job_opening_state


@admin.register(JobOpening)
class JobOpeningAdmin(admin.ModelAdmin):
    form = JobOpeningAdminForm
    list_display = (
        "title",
        "venture",
        "opportunity_type",
        "department",
        "work_mode",
        "publication_state",
        "number_of_positions",
        "application_closing_date",
    )
    list_filter = (
        "venture",
        "opportunity_type",
        "work_mode",
        "erasmus_plus_applicable",
        "is_active",
    )
    search_fields = (
        "public_slug",
        "recruitment__title",
        "recruitment__description",
        "recruitment__job_position_id__job_position",
    )
    readonly_fields = ("public_id", "created_at")
    filter_horizontal = ("preferred_skills",)

    def save_model(self, request, obj, form, change):
        if not request.user.has_perm("recruitment.change_recruitment"):
            raise PermissionDenied

        requested_state = form.cleaned_data["publication_state"]
        previous_state = (
            JobOpening.all_objects.select_related("recruitment")
            .get(pk=obj.pk)
            .publication_state
            if change
            else JobOpening.PublicationState.DRAFT
        )
        required_state_permission = {
            JobOpening.PublicationState.PUBLISHED: "tvg_careers.publish_jobopening",
            JobOpening.PublicationState.CLOSED: "tvg_careers.close_jobopening",
        }.get(requested_state)
        if (
            requested_state != previous_state
            and required_state_permission
            and not request.user.has_perm(required_state_permission)
        ):
            raise PermissionDenied

        form.apply_recruitment_changes()
        super().save_model(request, obj, form, change)
        if requested_state != obj.publication_state:
            set_job_opening_state(
                actor=request.user,
                opening=obj,
                state=requested_state,
            )


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    form = ApplicationAdminForm
    list_display = (
        "person",
        "candidate",
        "opening",
        "stage",
        "submitted_at",
        "withdrawn_at",
        "source",
        "public_id",
    )
    list_filter = ("opening__venture", "is_active")
    search_fields = (
        "public_id",
        "person__primary_email",
        "candidate__name",
        "candidate__email",
        "opening__public_slug",
    )
    readonly_fields = (
        "public_id",
        "created_at",
        "withdrawn_at",
        "questionnaire_version",
        "questionnaire_snapshot",
        "source",
        "utm_parameters",
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and request.user.has_perm(
            "tvg_careers.link_candidate_application"
        )

    def save_model(self, request, obj, form, change):
        identity_link_changed = not change or any(
            field in form.changed_data for field in ("person", "candidate", "opening")
        )
        if identity_link_changed and not request.user.has_perm(
            "tvg_careers.link_candidate_application"
        ):
            raise PermissionDenied
        super().save_model(request, obj, form, change)


@admin.register(RecruitmentQuestionnaireVersion)
class RecruitmentQuestionnaireVersionAdmin(admin.ModelAdmin):
    list_display = ("recruitment", "version_number", "fingerprint", "created_at")
    search_fields = ("recruitment__title", "public_id", "fingerprint")
    readonly_fields = (
        "public_id",
        "recruitment",
        "version_number",
        "fingerprint",
        "definition_snapshot",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApplicationConfirmationEvent)
class ApplicationConfirmationEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "application", "occurred_at", "published_at")
    search_fields = ("event_id", "application__public_id")
    readonly_fields = (
        "event_id",
        "application",
        "event_type",
        "schema_version",
        "payload",
        "occurred_at",
        "published_at",
        "delivery_attempts",
        "last_error",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
