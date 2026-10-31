from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import get_template
from django.urls import reverse_lazy
from django.utils import timezone
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


NAV_KEYS = {
    ("peoplehub", "dashboard"): "dashboard",
    ("peoplehub", "policy-detail"): "dashboard",
    ("peoplehub", "my-profile"): "profile",
    ("peoplehub", "directory"): "directory",
    ("peoplehub", "leave-create"): "leave",
    ("peoplehub", "request-create"): "requests",
    ("peoplehub", "request-queue"): "queue",
    ("peoplehub", "team"): "team",
    ("peoplehub", "team-member"): "team",
    ("reporting", "review-queue"): "reviews",
    ("reporting", "management"): "reporting-status",
}
NAMESPACE_NAV_KEYS = {"reporting": "reporting", "completion": "completion"}


def _nav_active(request):
    match = getattr(request, "resolver_match", None)
    if not match:
        return ""
    return NAV_KEYS.get(
        (match.namespace, match.url_name), NAMESPACE_NAV_KEYS.get(match.namespace, "")
    )


def _initials(employee):
    parts = [
        getattr(employee, "employee_first_name", "") or "",
        getattr(employee, "employee_last_name", "") or "",
    ]
    return "".join(part[:1] for part in parts if part).upper() or "?"


def _render(request, template_name, context, *, status=200):
    """Render without Horilla's global admin/menu context processors."""
    context = dict(context)
    context["user"] = request.user
    context["csrf_token"] = get_token(request)
    context["nav_active"] = _nav_active(request)
    if context.get("portal_employee"):
        context["portal_initials"] = _initials(context["portal_employee"])
    return HttpResponse(get_template(template_name).render(context), status=status)


OPEN_REQUEST_STATUSES = {"new", "in_progress", "on_hold"}
OPEN_TASK_STATUSES = {"to_do", "in_progress"}


def _dashboard_summary(data, today):
    """Small, derived figures for the dashboard header and tiles."""

    summary = {
        "leave_available": sum(
            item.available_days or 0 for item in data["leave_balances"]
        ),
        "open_requests": sum(
            1 for item in data["requests"] if item.status in OPEN_REQUEST_STATUSES
        ),
        "open_tasks": sum(
            1 for item in data["tasks"] if item.status in OPEN_TASK_STATUSES
        ),
        "learning_open": 0,
        "learning_overdue": 0,
        "next_report": None,
        "internship": None,
        "onboarding": None,
        "next_actions": [],
    }
    for item in data["learning"]:
        if item.status != "completed":
            summary["learning_open"] += 1
            if item.due_date and item.due_date < today:
                summary["learning_overdue"] += 1
    upcoming = sorted(
        (
            item
            for item in data["reporting"]
            if item.status != "completed" and item.due_date
        ),
        key=lambda item: item.due_date,
    )
    summary["next_report"] = upcoming[0] if upcoming else None

    actions = []

    def add_action(*, title, label, due_date, href, status="pending"):
        actions.append(
            {
                "title": title,
                "label": label,
                "due_date": due_date,
                "href": href,
                "status": status,
            }
        )

    for item in data["reporting"]:
        if item.status != "completed":
            add_action(
                title=item.title,
                label=item.get_kind_display(),
                due_date=item.due_date,
                href="#reporting-section",
                status=(
                    "overdue" if item.due_date and item.due_date < today else "pending"
                ),
            )
    for item in data["learning"]:
        if item.status != "completed":
            add_action(
                title=item.title,
                label="Training",
                due_date=item.due_date,
                href="#learning",
                status=(
                    "overdue" if item.due_date and item.due_date < today else "pending"
                ),
            )
    for item in data["tasks"]:
        if item.status in OPEN_TASK_STATUSES:
            add_action(
                title=item.title,
                label="Project task",
                due_date=item.end_date,
                href="#tasks",
                status=(
                    "overdue" if item.end_date and item.end_date < today else "pending"
                ),
            )

    checklist = list(data.get("onboarding_checklist", ()))
    if checklist:
        required = [item for item in checklist if item.required]
        completed = [
            item
            for item in required
            if item.completed_at or item.native_task.status == "done"
        ]
        overdue = 0
        for item in required:
            if item.completed_at or item.native_task.status == "done":
                continue
            is_overdue = bool(item.due_date and item.due_date < today)
            overdue += int(is_overdue)
            add_action(
                title=item.native_task.onboarding_task_id.task_title,
                label="Onboarding",
                due_date=item.due_date,
                href="#onboarding-progress",
                status="overdue" if is_overdue else "pending",
            )
        summary["onboarding"] = {
            "completed": len(completed),
            "total": len(required),
            "overdue": overdue,
            "percent": round(len(completed) * 100 / len(required)) if required else 100,
        }

    actions.sort(
        key=lambda item: (
            item["status"] != "overdue",
            item["due_date"] is None,
            item["due_date"] or today,
            item["title"],
        )
    )
    summary["next_actions"] = actions[:6]
    onboarding = data.get("onboarding")
    if onboarding and onboarding.start_date and onboarding.end_date:
        total = max((onboarding.end_date - onboarding.start_date).days, 1)
        elapsed = min(max((today - onboarding.start_date).days, 0), total)
        summary["internship"] = {
            "percent": round(elapsed * 100 / total),
            "days_left": max((onboarding.end_date - today).days, 0),
            "started": today >= onboarding.start_date,
            "days_to_start": max((onboarding.start_date - today).days, 0),
        }
    return summary


@login_required
def dashboard(request):
    employee, context = _base_context(request)
    data = dashboard_data(employee)
    today = timezone.localdate()
    context.update(data)
    context["today"] = today
    context["summary"] = _dashboard_summary(data, today)
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
        request,
        "tvg_peoplehub/form.html",
        context
        | {
            "title": "Request leave",
            "intro": "Choose the leave type and dates. Your manager is notified "
            "and you can follow the status on your dashboard.",
            "submit_label": "Submit leave request",
        },
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
        context
        | {
            "title": "Create an internal request",
            "intro": "Ask HR, IT or operations for help. You can follow the "
            "status under My requests on your dashboard.",
            "submit_label": "Submit request",
        },
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
