"""Self-service password changes for already provisioned employee accounts.

No account creation or password-reset-by-identity endpoint is exposed here.
"""

from django.contrib.auth.views import PasswordChangeView
from django.db import transaction
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template.loader import get_template
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from .services import employee_for_user


@method_decorator(never_cache, name="dispatch")
class PeopleHubPasswordChangeView(PasswordChangeView):
    """Keep Django's old-password, validators, CSRF and session invalidation."""

    template_name = "tvg_peoplehub/password_change.html"
    success_url = reverse_lazy("peoplehub:dashboard")

    def get_form_kwargs(self):
        # A staff session alone is insufficient: the portal requires an active
        # Employee link. Never accept a target user from POST data.
        employee_for_user(self.request.user)
        return super().get_form_kwargs()

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        # Satisfy Horilla's existing first-login gate only after validation and
        # password persistence succeed. Do not bypass it in the login view.
        form.user.is_new_employee = False
        form.user.save(update_fields=("is_new_employee",))
        return response

    def render_to_response(self, context, **response_kwargs):
        # Use the same narrow rendering boundary as the rest of PeopleHub,
        # without importing the full HR navigation context processors.
        context["user"] = self.request.user
        context["csrf_token"] = get_token(self.request)
        return HttpResponse(
            get_template(self.template_name).render(context), **response_kwargs
        )
