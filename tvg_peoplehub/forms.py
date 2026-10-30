from django import forms
from django.core.exceptions import ValidationError

from leave.models import LeaveRequest, LeaveType

from .services import available_request_types


class PeopleHubLeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = (
            "leave_type_id",
            "start_date",
            "start_date_breakdown",
            "end_date",
            "end_date_breakdown",
            "description",
        )
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, employee, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        self.fields["leave_type_id"].queryset = LeaveType._base_manager.filter(
            employee_available_leave__employee_id=employee
        ).distinct()

    def _post_clean(self):
        self.instance.employee_id = self.employee
        self.instance.created_by = self.employee
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.employee_id = self.employee
        instance.created_by = self.employee
        instance.full_clean()
        if commit:
            instance.save()
        return instance


class PeopleHubRequestForm(forms.Form):
    request_type = forms.ModelChoiceField(queryset=None)
    title = forms.CharField(max_length=50)
    description = forms.CharField(max_length=255, widget=forms.Textarea)
    priority = forms.ChoiceField(
        choices=(("low", "Low"), ("medium", "Medium"), ("high", "High"))
    )

    def __init__(self, *args, employee, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        self.fields["request_type"].queryset = available_request_types(employee)

    def clean_request_type(self):
        request_type = self.cleaned_data["request_type"]
        if (
            not available_request_types(self.employee)
            .filter(pk=request_type.pk)
            .exists()
        ):
            raise ValidationError("This request type is not available to you.")
        return request_type
