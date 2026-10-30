from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import get_template
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods

from employee.models import Employee
from helpdesk.models import Ticket

from .auth import PeopleHubAuthenticationForm
from .forms import PeopleHubLeaveRequestForm, PeopleHubRequestForm
from .services import (
    can_view_employee,
    company_id_for,
    dashboard_data,
    directory_rows,
    employee_for_user,
    portal_roles,
    relevant_policies,
    submit_request,
    team_for,
    venture_for,
)


def health(request):
    return JsonResponse({"status": "ok", "service": "peoplehub"})


def missing_file(request):
    """Safe target used by Horilla's protected file-field placeholder."""
    return HttpResponse("Not found", status=404, content_type="text/plain")


def permission_denied(request, exception=None):
    return HttpResponse("Forbidden", status=403, content_type="text/plain")


def page_not_found(request, exception=None):
    return HttpResponse("Not found", status=404, content_type="text/plain")


class PeopleHubLoginView(auth_views.LoginView):
    authentication_form = PeopleHubAuthenticationForm
    template_name = "tvg_peoplehub/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or str(reverse_lazy("peoplehub:dashboard"))

    def render_to_response(self, context, **response_kwargs):
        context["user"] = self.request.user
        context["csrf_token"] = get_token(self.request)
        return HttpResponse(
            get_template(self.template_name).render(context),
            **response_kwargs,
        )


class PeopleHubLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("peoplehub:login")


def _base_context(request):
    employee = employee_for_user(request.user)
    roles = portal_roles(request.user, employee)
    return employee, {
        "portal_employee": employee,
        "portal_roles": roles,
        "reporting_permissions": {
            "review": roles.supervisor
            or request.user.has_perm("tvg_reporting.review_weekly_report")
            or request.user.has_perm("tvg_reporting.review_monthly_presentation"),
            "management": request.user.has_perm(
                "tvg_reporting.view_management_reporting"
            ),
        },
    }


def _render(request, template_name, context, *, status=200):
    """Render without Horilla's global admin/menu context processors."""
    context = dict(context)
    context["user"] = request.user
    context["csrf_token"] = get_token(request)
    return HttpResponse(get_template(template_name).render(context), status=status)


@login_required
def dashboard(request):
    employee, context = _base_context(request)
    context.update(dashboard_data(employee))
    return _render(request, "tvg_peoplehub/dashboard.html", context)


@login_required
def my_profile(request):
    employee, context = _base_context(request)
    context["profile"] = {
        "name": employee.get_full_name(),
        "personal_email": employee.email,
        "phone": employee.phone,
        "address": employee.address or "",
        "city": employee.city or "",
        "state": employee.state or "",
        "country": employee.country or "",
        "postal_code": employee.zip or "",
    }
    return _render(request, "tvg_peoplehub/my_profile.html", context)


@login_required
def directory(request):
    employee, context = _base_context(request)
    context["directory_rows"] = directory_rows(employee)
    return _render(request, "tvg_peoplehub/directory.html", context)


@login_required
def policy_detail(request, pk):
    employee, context = _base_context(request)
    policy = get_object_or_404(relevant_policies(employee), pk=pk)
    context["policy"] = policy
    return _render(request, "tvg_peoplehub/policy_detail.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def leave_create(request):
    employee, context = _base_context(request)
    form = PeopleHubLeaveRequestForm(
        request.POST or None,
        employee=employee,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("peoplehub:dashboard")
    context["form"] = form
    return _render(
        request, "tvg_peoplehub/form.html", context | {"title": "Request leave"}
    )


@login_required
@require_http_methods(["GET", "POST"])
def request_create(request):
    employee, context = _base_context(request)
    form = PeopleHubRequestForm(request.POST or None, employee=employee)
    if request.method == "POST" and form.is_valid():
        submit_request(employee=employee, **form.cleaned_data)
        return redirect("peoplehub:dashboard")
    context["form"] = form
    return _render(
        request,
        "tvg_peoplehub/form.html",
        context | {"title": "Create an internal request"},
    )


@login_required
def team(request):
    employee, context = _base_context(request)
    roles = context["portal_roles"]
    if not (roles.supervisor or roles.hr or roles.management):
        raise PermissionDenied("Team access is not assigned.")
    context["team_members"] = team_for(request.user, employee)
    return _render(request, "tvg_peoplehub/team.html", context)


@login_required
def team_member(request, pk):
    viewer, context = _base_context(request)
    target = get_object_or_404(
        Employee._base_manager.select_related(
            "employee_work_info__department_id",
            "employee_work_info__job_position_id",
            "employee_work_info__reporting_manager_id",
        ),
        pk=pk,
        is_active=True,
    )
    if not can_view_employee(request.user, viewer, target):
        raise PermissionDenied("You cannot view this employee.")
    work = target.employee_work_info
    context["member"] = {
        "name": target.get_full_name(),
        "company_email": work.email or "",
        "position": work.job_position_id.job_position if work.job_position_id else "",
        "department": work.department_id.department if work.department_id else "",
        "venture": venture_for(target),
        "manager": work.reporting_manager_id,
        "work_location": work.location or "",
    }
    return _render(request, "tvg_peoplehub/team_member.html", context)


@login_required
def request_queue(request):
    employee, context = _base_context(request)
    if not (request.user.is_superuser or request.user.has_perm("helpdesk.view_ticket")):
        raise PermissionDenied("Request queue permission is required.")
    context["queue"] = Ticket._base_manager.filter(
        employee_id__employee_work_info__company_id_id=company_id_for(employee),
        peoplehub_request_link__isnull=False,
    ).select_related("employee_id", "ticket_type")
    return _render(request, "tvg_peoplehub/request_queue.html", context)
