from django.core.exceptions import PermissionDenied
from django.db import transaction

from base.auth_backends import get_allowed_company_ids
from tvg_core.models import Person


def _require_permission(actor, permission):
    if not getattr(actor, "is_authenticated", False) or not actor.has_perm(permission):
        raise PermissionDenied


def _require_company_access(actor, company_id):
    if getattr(actor, "is_superuser", False):
        return
    if company_id not in get_allowed_company_ids(actor):
        raise PermissionDenied


@transaction.atomic
def link_person_to_employee(*, actor, person: Person, employee) -> Person:
    """Link identity to an Employee without bypassing company validation."""

    _require_permission(actor, "tvg_core.link_person_employee")
    _require_company_access(actor, person.legal_employer_id)
    locked = Person.all_objects.select_for_update(of=("self",)).get(pk=person.pk)
    locked.employee = employee
    locked.save()
    return locked
