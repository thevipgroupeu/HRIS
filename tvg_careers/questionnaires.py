"""Versioning adapter around Horilla's native recruitment survey models."""

from __future__ import annotations

import hashlib
import json
import uuid

from django.db import transaction

from recruitment.models import Recruitment, RecruitmentSurvey, SurveyTemplate
from tvg_careers.models import (
    ApplicationConfirmationEvent,
    RecruitmentQuestionnaireVersion,
)

CAREERHUB_QUESTION_TYPES = {
    "checkbox",
    "options",
    "multiple",
    "text",
    "number",
    "percentage",
    "date",
    "textarea",
    "rating",
}


class QuestionnaireConfigurationError(ValueError):
    pass


def _native_definition(recruitment):
    templates = list(
        SurveyTemplate._base_manager.filter(recruitment=recruitment).order_by("pk")
    )
    invalid_templates = [
        template
        for template in templates
        if template.company_id_id != recruitment.company_id_id
        and not (template.is_general_template and template.company_id_id is None)
    ]
    if invalid_templates:
        raise QuestionnaireConfigurationError(
            "Every questionnaire template must belong to the recruitment company."
        )

    questions = list(
        RecruitmentSurvey._base_manager.filter(template_id__in=templates)
        .distinct()
        .prefetch_related("template_id")
        .order_by("sequence", "pk")
    )
    labels = set()
    definition = []
    for question in questions:
        foreign_templates = [
            template
            for template in question.template_id.all()
            if template.company_id_id != recruitment.company_id_id
            and not (template.is_general_template and template.company_id_id is None)
        ]
        if foreign_templates:
            raise QuestionnaireConfigurationError(
                "Questionnaire questions cannot be shared across companies."
            )
        if question.type == "file":
            raise QuestionnaireConfigurationError(
                "CareerHub does not accept generic survey file questions."
            )
        if question.type not in CAREERHUB_QUESTION_TYPES:
            raise QuestionnaireConfigurationError(
                f"Unsupported CareerHub question type: {question.type}."
            )
        label = str(question.question).strip()
        if not label or label.casefold() in labels:
            raise QuestionnaireConfigurationError(
                "Question labels must be present and unique per recruitment."
            )
        labels.add(label.casefold())
        options = []
        if question.type in {"options", "multiple"}:
            options = [
                option.strip()
                for option in str(question.options or "").split(",")
                if option.strip()
            ]
            if not options:
                raise QuestionnaireConfigurationError(
                    "Choice questions require at least one option."
                )
        definition.append(
            {
                "native_question_id": question.pk,
                "label": label,
                "type": question.type,
                "required": bool(question.is_mandatory),
                "options": options,
                "sequence": question.sequence or 0,
            }
        )
    return definition


def _fingerprint(definition):
    canonical = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _existing_public_ids(recruitment):
    result = {}
    versions = RecruitmentQuestionnaireVersion.all_objects.filter(
        recruitment=recruitment
    ).order_by("-version_number")
    for version in versions:
        for question in version.definition_snapshot:
            native_id = question.get("native_question_id")
            public_id = question.get("public_id")
            if native_id and public_id and native_id not in result:
                result[native_id] = public_id
    return result


@transaction.atomic
def ensure_questionnaire_version(recruitment):
    """Return the immutable version matching current native survey configuration."""

    recruitment = Recruitment.default.select_for_update(of=("self",)).get(pk=recruitment.pk)
    native_definition = _native_definition(recruitment)
    fingerprint = _fingerprint(native_definition)
    existing = RecruitmentQuestionnaireVersion.all_objects.filter(
        recruitment=recruitment,
        fingerprint=fingerprint,
    ).first()
    if existing:
        return existing

    public_ids = _existing_public_ids(recruitment)
    definition = []
    for question in native_definition:
        question = dict(question)
        question["public_id"] = public_ids.get(
            question["native_question_id"], str(uuid.uuid4())
        )
        definition.append(question)
    latest = (
        RecruitmentQuestionnaireVersion.all_objects.filter(recruitment=recruitment)
        .order_by("-version_number")
        .first()
    )
    return RecruitmentQuestionnaireVersion.all_objects.create(
        recruitment=recruitment,
        version_number=(latest.version_number + 1 if latest else 1),
        fingerprint=fingerprint,
        definition_snapshot=definition,
    )


def current_questionnaire_version(recruitment):
    """Resolve the stored version matching native configuration without writing."""

    definition = _native_definition(recruitment)
    return RecruitmentQuestionnaireVersion.all_objects.filter(
        recruitment=recruitment,
        fingerprint=_fingerprint(definition),
    ).first()


def public_questionnaire(version):
    return [
        {
            "id": question["public_id"],
            "label": question["label"],
            "type": question["type"],
            "required": question["required"],
            "options": list(question.get("options", [])),
        }
        for question in version.definition_snapshot
    ]


def questionnaire_version_payload(version):
    return {
        "id": str(version.public_id),
        "number": version.version_number,
        "fingerprint": version.fingerprint,
    }


@transaction.atomic
def enqueue_application_confirmation(application):
    """Create the idempotent, data-minimized submission confirmation event."""

    event, _created = ApplicationConfirmationEvent.all_objects.get_or_create(
        application=application,
        defaults={
            "payload": {
                "application_id": str(application.public_id),
                "opening_id": str(application.opening.public_id),
                "person_id": str(application.person.public_id),
                "venture": application.opening.venture.slug,
                "source": application.source,
            }
        },
    )
    # Phase 15 wraps the existing domain event in the central transactional
    # outbox. This call is intentionally synchronous and database-only, so the
    # application and its dispatch record commit or roll back together.
    from tvg_automation.services import register_application_acknowledgment

    register_application_acknowledgment(event)
    return event
