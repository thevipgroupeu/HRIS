from django import forms

from tvg_core.models import Person, Venture


class VentureAdminForm(forms.ModelForm):
    class Meta:
        model = Venture
        fields = (
            "legal_employer",
            "name",
            "slug",
            "summary",
            "display_order",
            "is_active",
        )


class PersonAdminForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = (
            "legal_employer",
            "primary_email",
            "preferred_name",
            "employee",
            "is_active",
        )

    def clean_employee(self):
        employee = self.cleaned_data.get("employee")
        company = self.cleaned_data.get("legal_employer")
        if employee and company and employee.get_company() != company:
            raise forms.ValidationError(
                "Employee record must belong to the selected legal employer."
            )
        return employee
