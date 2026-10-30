from django.contrib import admin
from django.core.exceptions import PermissionDenied

from tvg_core.forms import PersonAdminForm, VentureAdminForm
from tvg_core.models import Person, Venture


@admin.register(Venture)
class VentureAdmin(admin.ModelAdmin):
    form = VentureAdminForm
    list_display = ("name", "slug", "legal_employer", "is_active", "display_order")
    list_filter = ("legal_employer", "is_active")
    search_fields = ("name", "slug", "legal_employer__company")


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    form = PersonAdminForm
    list_display = (
        "primary_email",
        "preferred_name",
        "legal_employer",
        "employee",
        "public_id",
        "is_active",
    )
    list_filter = ("legal_employer", "is_active")
    search_fields = (
        "primary_email",
        "preferred_name",
        "public_id",
        "employee__employee_first_name",
        "employee__employee_last_name",
    )
    readonly_fields = ("public_id", "created_at")

    def save_model(self, request, obj, form, change):
        employee_changed = "employee" in form.changed_data
        if employee_changed and not request.user.has_perm(
            "tvg_core.link_person_employee"
        ):
            raise PermissionDenied
        super().save_model(request, obj, form, change)
