from django.core.exceptions import PermissionDenied
from django.db import transaction

from base.auth_backends import get_allowed_company_ids
from recruitment.models import Recruitment
from tvg_careers.models import Application, JobOpening
from tvg_careers.questionnaires import ensure_questionnaire_version


def _require_permission(actor, permission):
    if not getattr(actor, "is_authenticated", False) or not actor.has_perm(permission):
        raise PermissionDenied


def _require_any_permission(actor, *permissions):
    if not getattr(actor, "is_authenticated", False) or not any(
        actor.has_perm(permission) for permission in permissions
    ):
        raise PermissionDenied


def _require_company_access(actor, company_id):
    if getattr(actor, "is_superuser", False):
        return
    if company_id not in get_allowed_company_ids(actor):
        raise PermissionDenied


@transaction.atomic
def set_job_opening_state(*, actor, opening: JobOpening, state: str) -> JobOpening:
    permission_by_state = {
        JobOpening.PublicationState.DRAFT: "tvg_careers.change_jobopening",
        JobOpening.PublicationState.PUBLISHED: "tvg_careers.publish_jobopening",
        JobOpening.PublicationState.CLOSED: "tvg_careers.close_jobopening",
    }
    try:
        permission = permission_by_state[state]
    except KeyError as exc:
        raise ValueError(f"Unsupported job opening state: {state}") from exc
    if state == JobOpening.PublicationState.DRAFT:
        _require_any_permission(
            actor,
            "tvg_careers.add_jobopening",
            "tvg_careers.change_jobopening",
        )
    else:
        _require_permission(actor, permission)
    _require_company_access(actor, opening.venture.legal_employer_id)

    recruitment = Recruitment.default.select_for_update(of=("self",)).get(pk=opening.recruitment_id)
    if state == JobOpening.PublicationState.DRAFT:
        recruitment.closed = False
        recruitment.is_active = True
        recruitment.is_published = False
    elif state == JobOpening.PublicationState.PUBLISHED:
        recruitment.closed = False
        recruitment.is_active = True
        recruitment.is_published = True
    else:
        recruitment.closed = True
        recruitment.is_published = False

    recruitment.full_clean()
    opening.recruitment = recruitment
    opening.full_clean()
    recruitment.save()
    if state == JobOpening.PublicationState.PUBLISHED:
        ensure_questionnaire_version(recruitment)
    return JobOpening.all_objects.select_related("recruitment", "venture").get(
        pk=opening.pk
    )


@transaction.atomic
def link_candidate_application(*, actor, person, candidate, opening) -> Application:
    _require_permission(actor, "tvg_careers.link_candidate_application")
    _require_company_access(actor, opening.venture.legal_employer_id)
    application = Application(person=person, candidate=candidate, opening=opening)
    application.save()
    return application
