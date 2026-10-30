from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class PeopleHubAuthenticationForm(AuthenticationForm):
    """Local Horilla login limited to active employee identities."""

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        try:
            employee = user.employee_get
            company = employee.employee_work_info.company_id
        except Exception:
            employee = company = None
        if not employee or not employee.is_active or company is None:
            raise ValidationError(
                _("PeopleHub requires an active employee account."),
                code="inactive_employee",
            )
