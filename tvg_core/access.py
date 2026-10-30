"""Explicit object scopes for the narrow hooks in native Horilla views."""

from django.db.models import Q

from base.auth_backends import get_assigned_company_ids, get_user_groups_for_company
from employee.models import Employee


def active_employee(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    return (
        Employee._base_manager.select_related("employee_work_info")
        .filter(
            employee_user_id=user,
            is_active=True,
            employee_work_info__company_id__isnull=False,
        )
        .first()
    )


def permitted_companies(user, permission):
    """A model permission is not a grant to read every tenant's records."""
    employee = active_employee(user)
    if not employee:
        return set()
    app_label, codename = permission.split(".", 1)
    companies = get_assigned_company_ids(user) | {
        employee.employee_work_info.company_id_id
    }
    direct = user.user_permissions.filter(
        content_type__app_label=app_label, codename=codename
    ).exists()
    if user.is_superuser or direct:
        return companies
    return {
        company
        for company in companies
        if get_user_groups_for_company(user, company)
        .filter(
            permissions__content_type__app_label=app_label,
            permissions__codename=codename,
        )
        .exists()
    }


def recruitment_scope(queryset, user, permission, company_field):
    return queryset.filter(
        **{f"{company_field}__in": permitted_companies(user, permission)}
    )


def can_download_media(user, path):
    """Only explicitly supported native owners; unowned/unknown files fail closed.

    TVG legal/evidence documents retain their dedicated download services and
    private storage. Do not add generic permission-only fallback for other files.
    """
    from recruitment.models import Candidate, CandidateDocument
    from tvg_peoplehub.services import relevant_policies

    employee = active_employee(user)
    if not employee or not path:
        return False
    candidate_files = Candidate._base_manager.filter(Q(resume=path) | Q(profile=path))
    if candidate_files.exists():
        return recruitment_scope(
            candidate_files,
            user,
            "recruitment.view_candidate",
            "recruitment_id__company_id",
        ).exists()
    documents = CandidateDocument._base_manager.filter(document=path)
    if documents.exists():
        return recruitment_scope(
            documents,
            user,
            "recruitment.view_candidatedocument",
            "candidate_id__recruitment_id__company_id",
        ).exists()
    if employee.employee_profile and employee.employee_profile.name == path:
        return True
    return relevant_policies(employee).filter(attachments__attachment=path).exists()
