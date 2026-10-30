from django.contrib import admin

from .models import (
    MoodleAccountLink,
    MoodleEnrollment,
    StaffIdentityLink,
    StaffIdentitySession,
    StaffRoleMapping,
    TrainingAssignment,
    TrainingCourse,
    TrainingEvent,
    TrainingNotification,
    TrainingProgramme,
    TrainingProgrammeCourse,
    TrainingSyncAttempt,
)


class TrainingProgrammeCourseInline(admin.TabularInline):
    model = TrainingProgrammeCourse
    extra = 0


@admin.register(TrainingProgramme)
class TrainingProgrammeAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "version",
        "category",
        "audience",
        "company",
        "is_active",
        "approved_at",
    )
    list_filter = ("category", "audience", "is_active", "company")
    readonly_fields = ("approved_by", "approved_at")
    inlines = (TrainingProgrammeCourseInline,)


@admin.register(TrainingAssignment)
class TrainingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "programme", "status", "due_date", "last_error")
    list_filter = ("status", "programme__company", "programme__category")
    search_fields = ("employee__email", "programme__title", "idempotency_key")
    readonly_fields = tuple(field.name for field in TrainingAssignment._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyTrainingAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(TrainingCourse)
admin.site.register(MoodleAccountLink, ReadOnlyTrainingAdmin)
admin.site.register(MoodleEnrollment, ReadOnlyTrainingAdmin)
admin.site.register(TrainingSyncAttempt, ReadOnlyTrainingAdmin)
admin.site.register(TrainingNotification, ReadOnlyTrainingAdmin)
admin.site.register(StaffIdentityLink, ReadOnlyTrainingAdmin)
admin.site.register(StaffRoleMapping)
admin.site.register(StaffIdentitySession, ReadOnlyTrainingAdmin)
admin.site.register(TrainingEvent, ReadOnlyTrainingAdmin)
