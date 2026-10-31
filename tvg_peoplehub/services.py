from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from asset.models import AssetAssignment
from attendance.models import Attendance
from base.models import Announcement, Holidays
from employee.models import Employee, Policy
from helpdesk.models import Ticket
from leave.models import AvailableLeave, LeaveRequest
from pms.models import EmployeeObjective, Feedback
from project.models import Task
from tvg_erasmus.models import ErasmusAccommodationRequest

from .models import (
    PeopleHubEvent,
    PeopleHubLearningAssignment,
    PeopleHubRequestLink,
    PeopleHubRequestType,
)


@dataclass(frozen=True)
class PortalRoles:
    employee: bool = True
    supervisor: bool = False
    hr: bool = False
    it_administrator: bool = False
    management: bool = False

    def as_dict(self):
        return {
            "employee": self.employee,
            "supervisor": self.supervisor,
            "hr": self.hr,
            "it_administrator": self.it_administrator,
            "management": self.management,
        }


def employee_for_user(user) -> Employee:
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("PeopleHub authentication is required.")
    try:
        employee = Employee._base_manager.select_related(
            "employee_work_info__company_id"
        ).get(
            employee_user_id=user,
            is_active=True,
            employee_work_info__company_id__isnull=False,
        )
    except Employee.DoesNotExist:
        raise PermissionDenied("An active employee record is required.") from None
    return employee


def company_id_for(employee: Employee) -> int:
    return employee.employee_work_info.company_id_id


def portal_roles(user, employee: Employee) -> PortalRoles:
    company_id = company_id_for(employee)
    supervisor = Employee._base_manager.filter(
        employee_work_info__company_id_id=company_id,
        employee_work_info__reporting_manager_id=employee,
        is_active=True,
    ).exists()
    return PortalRoles(
        supervisor=supervisor,
        hr=user.is_superuser or user.has_perm("employee.view_employee"),
        it_administrator=user.is_superuser or user.has_perm("helpdesk.view_ticket"),
        management=user.is_superuser
        or user.has_perm("base.view_company")
        or user.has_perm("pms.view_employeeobjective"),
    )


def venture_for(employee: Employee):
    onboarding = getattr(employee, "tvg_intern_onboarding", None)
    if onboarding:
        return onboarding.venture
    assignment = getattr(employee, "tvg_venture_assignment", None)
    return assignment.venture if assignment else None


def internship_identity(employee: Employee):
    return getattr(employee, "tvg_intern_onboarding", None)


def relevant_policies(employee: Employee):
    work = employee.employee_work_info
    scope = (
        Q(is_visible_to_all=True)
        | Q(employees=employee)
        | Q(filtered_employees=employee)
    )
    if work.department_id_id:
        scope |= Q(department=work.department_id)
    if work.job_position_id_id:
        scope |= Q(job_position=work.job_position_id)
    return (
        Policy._base_manager.filter(company_id=work.company_id)
        .filter(scope)
        .distinct()
        .order_by("title")
    )


def relevant_announcements(employee: Employee):
    """Return current announcements explicitly visible to this employee.

    The native manager relies on request-local company context.  PeopleHub has
    a separate URL boundary, so this query applies the company and audience
    constraints explicitly as a defence-in-depth measure.
    """

    work = employee.employee_work_info
    audience = (
        Q(is_visible_to_all=True)
        | Q(employees=employee)
        | Q(filtered_employees=employee)
    )
    if work.department_id_id:
        audience |= Q(department=work.department_id)
    if work.job_position_id_id:
        audience |= Q(job_position=work.job_position_id)
    today = timezone.localdate()
    return (
        Announcement._base_manager.filter(company_id=work.company_id)
        .filter(Q(expire_date__isnull=True) | Q(expire_date__gte=today))
        .filter(audience)
        .distinct()
        .order_by("-created_at")[:5]
    )


def upcoming_holidays(employee: Employee, *, days=60):
    """Return company/global holidays applicable to the employee."""

    work = employee.employee_work_info
    today = timezone.localdate()
    horizon = today + timedelta(days=days)
    audience = Q(is_specific=False) | Q(employees=employee)
    if work.department_id_id:
        audience |= Q(department=work.department_id)
    if work.job_position_id_id:
        audience |= Q(job_position=work.job_position_id)
    return (
        Holidays._base_manager.filter(
            Q(company_id=work.company_id) | Q(company_id__isnull=True),
            Q(end_date__gte=today) | Q(end_date__isnull=True, start_date__gte=today),
            start_date__lte=horizon,
        )
        .filter(audience)
        .distinct()
        .order_by("start_date", "name")[:8]
    )


def can_view_employee(user, viewer: Employee, target: Employee) -> bool:
    if company_id_for(viewer) != company_id_for(target):
        return False
    if viewer.pk == target.pk:
        return True
    if target.employee_work_info.reporting_manager_id_id == viewer.pk:
        return True
    return user.is_superuser or user.has_perm("employee.view_employee")


def require_employee_access(user, viewer: Employee, target: Employee):
    if not can_view_employee(user, viewer, target):
        raise PermissionDenied("You cannot view this employee.")


def team_for(user, viewer: Employee):
    company_id = company_id_for(viewer)
    base = Employee._base_manager.filter(
        employee_work_info__company_id_id=company_id,
        is_active=True,
    ).select_related(
        "employee_work_info__department_id",
        "employee_work_info__job_position_id",
        "employee_work_info__reporting_manager_id",
    )
    if user.is_superuser or user.has_perm("employee.view_employee"):
        return base.order_by("employee_first_name", "employee_last_name")
    return base.filter(employee_work_info__reporting_manager_id=viewer).order_by(
        "employee_first_name", "employee_last_name"
    )


def directory_rows(viewer: Employee):
    """Return the explicit shared-directory allowlist and nothing else."""
    employees = Employee._base_manager.filter(
        employee_work_info__company_id_id=company_id_for(viewer),
        is_active=True,
    ).select_related(
        "employee_work_info__department_id",
        "employee_work_info__job_position_id",
        "employee_work_info__reporting_manager_id",
    )
    rows = []
    for employee in employees.order_by("employee_first_name", "employee_last_name"):
        work = employee.employee_work_info
        venture = venture_for(employee)
        rows.append(
            {
                "name": employee.get_full_name(),
                "company_email": work.email or "",
                "position": (
                    str(work.job_position_id.job_position)
                    if work.job_position_id
                    else ""
                ),
                "department": (
                    str(work.department_id.department) if work.department_id else ""
                ),
                "venture": venture.name if venture else "",
                "manager": (
                    work.reporting_manager_id.get_full_name()
                    if work.reporting_manager_id
                    else ""
                ),
                "work_location": work.location or "",
            }
        )
    return rows


def accommodation_is_relevant(employee: Employee) -> bool:
    onboarding = internship_identity(employee)
    if not onboarding:
        return False
    return ErasmusAccommodationRequest.all_objects.filter(
        case=onboarding.erasmus_case,
        needs_assistance=True,
    ).exists()


def available_request_types(employee: Employee):
    queryset = PeopleHubRequestType.all_objects.filter(
        company_id=company_id_for(employee),
        approved_at__isnull=False,
        is_active=True,
    )
    if not accommodation_is_relevant(employee):
        queryset = queryset.exclude(
            category=PeopleHubRequestType.Category.ACCOMMODATION
        )
    return queryset.order_by("category", "title")


@transaction.atomic
def approve_request_type(*, user, request_type: PeopleHubRequestType):
    actor = employee_for_user(user)
    request_type = PeopleHubRequestType.all_objects.select_for_update(of=("self",)).get(
        pk=request_type.pk
    )
    if request_type.company_id != company_id_for(actor):
        raise PermissionDenied("Request type belongs to another company.")
    if not (user.is_superuser or user.has_perm("tvg_peoplehub.approve_request_type")):
        raise PermissionDenied("Request-type approval permission is required.")
    if request_type.approved_at:
        return request_type
    request_type.approved_by = actor
    request_type.approved_at = timezone.now()
    request_type.save(update_fields=("approved_by", "approved_at"))
    return request_type


@transaction.atomic
def submit_request(*, employee: Employee, request_type, title, description, priority):
    request_type = PeopleHubRequestType.all_objects.select_for_update(of=("self",)).get(
        pk=request_type.pk
    )
    if request_type.company_id != company_id_for(employee):
        raise PermissionDenied("Request type belongs to another company.")
    if not request_type.is_active or not request_type.approved_at:
        raise ValidationError("Only approved active request types can be used.")
    if (
        request_type.category == PeopleHubRequestType.Category.ACCOMMODATION
        and not accommodation_is_relevant(employee)
    ):
        raise PermissionDenied("Accommodation support is not applicable.")
    ticket = Ticket._base_manager.create(
        title=title,
        employee_id=employee,
        ticket_type=request_type.native_ticket_type,
        description=description,
        priority=priority,
        assigning_type=request_type.assigning_type,
        raised_on=str(request_type.raised_on_id),
    )
    PeopleHubRequestLink.all_objects.create(
        request_type=request_type,
        native_ticket=ticket,
        submitted_by=employee,
    )
    PeopleHubEvent.all_objects.create(
        employee=employee,
        event_type="request.submitted",
        object_public_id=request_type.public_id,
        payload={"ticket_id": ticket.pk, "category": request_type.category},
    )
    return ticket


def dashboard_data(employee: Employee):
    from tvg_onboarding.models import OnboardingChecklistLink
    from tvg_training.models import TrainingNotification

    onboarding = internship_identity(employee)
    assignments = PeopleHubLearningAssignment.all_objects.filter(
        employee=employee
    ).select_related("native_task")
    reporting_kinds = (
        PeopleHubLearningAssignment.Kind.WEEKLY_REPORT,
        PeopleHubLearningAssignment.Kind.MONTHLY_PRESENTATION,
    )
    onboarding_checklist = OnboardingChecklistLink.all_objects.none()
    if onboarding:
        onboarding_checklist = (
            OnboardingChecklistLink.all_objects.filter(onboarding=onboarding)
            .select_related("native_task__onboarding_task_id")
            .order_by("due_date", "code")
        )
    return {
        "onboarding": onboarding,
        "venture": venture_for(employee),
        "policies": relevant_policies(employee),
        "announcements": relevant_announcements(employee),
        "holidays": upcoming_holidays(employee),
        "onboarding_checklist": onboarding_checklist,
        "attendance": Attendance._base_manager.filter(employee_id=employee).order_by(
            "-attendance_date"
        )[:10],
        "leave_balances": AvailableLeave._base_manager.filter(employee_id=employee),
        "leave_requests": LeaveRequest._base_manager.filter(
            employee_id=employee
        ).order_by("-requested_date")[:10],
        "assets": AssetAssignment._base_manager.filter(
            assigned_to_employee_id=employee,
            return_date__isnull=True,
        ).select_related("asset_id", "asset_item_id"),
        "tasks": Task._base_manager.filter(task_members=employee).distinct()[:20],
        "objectives": EmployeeObjective._base_manager.filter(
            employee_id=employee, archive=False
        )[:20],
        "feedback": Feedback._base_manager.filter(employee_id=employee, archive=False)[
            :20
        ],
        "learning": assignments.exclude(kind__in=reporting_kinds),
        "training_notifications": TrainingNotification.all_objects.filter(
            supervisor=employee, acknowledged_at__isnull=True
        ).select_related(
            "enrollment__assignment__employee",
            "enrollment__programme_course__course",
        ),
        "reporting": assignments.filter(kind__in=reporting_kinds),
        "requests": Ticket._base_manager.filter(employee_id=employee).order_by(
            "-created_date", "-pk"
        )[:20],
    }
