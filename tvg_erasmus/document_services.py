"""Controlled Erasmus+ document lifecycle services.

All state changes pass through this module.  Files remain private and neither
template copying nor a signing request constitutes a legal signature.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from base.auth_backends import get_allowed_company_ids
from employee.models import Employee
from tvg_careers.document_security import inspect_candidate_document
from tvg_erasmus.models import (
    ErasmusAccommodationRequest,
    ErasmusCase,
    ErasmusCaseDocument,
    ErasmusDocumentEvent,
    ErasmusDocumentRequirement,
    ErasmusDocumentSignature,
    ErasmusDocumentTemplate,
    ErasmusDocumentVersion,
    ErasmusSigningRequest,
)
from tvg_erasmus.services import visible_erasmus_cases

ACCOMMODATION_QUESTION = "Do you need assistance finding accommodation in Kraków?"


def _require_company_permission(user, company, permission):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    if not employee:
        raise PermissionDenied
    if not user.is_superuser and (
        company.pk not in get_allowed_company_ids(user) or not user.has_perm(permission)
    ):
        raise PermissionDenied
    if employee.get_company() != company:
        raise PermissionDenied
    return employee


def _require_case_access(user, case, permission):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    employee = Employee._base_manager.filter(employee_user_id=user).first()
    if not employee:
        raise PermissionDenied
    if user.is_superuser:
        return employee
    application = case.application
    company_id = application.opening.venture.legal_employer_id
    if company_id not in get_allowed_company_ids(user) or not user.has_perm(permission):
        raise PermissionDenied
    related = case.supervisor_id == employee.pk
    related = (
        related
        or application.opening.recruitment.recruitment_managers.filter(
            pk=employee.pk
        ).exists()
    )
    if application.candidate.stage_id_id:
        related = (
            related
            or application.candidate.stage_id.stage_managers.filter(
                pk=employee.pk
            ).exists()
        )
    if not related:
        raise PermissionDenied
    return employee


def _require_document_access(user, document, permission):
    return _require_case_access(user, document.case, permission)


@transaction.atomic
def approve_document_template(*, user, template):
    template = (
        ErasmusDocumentTemplate.all_objects.select_for_update(of=("self",))
        .select_related("company")
        .get(pk=template.pk)
    )
    employee = _require_company_permission(
        user, template.company, "tvg_erasmus.approve_document_template"
    )
    if template.approved_at:
        raise ValidationError("This document template is already approved.")
    template.full_clean()
    try:
        with template.source_file.open("rb") as source:
            inspected = _inspect(source)
    except (FileNotFoundError, OSError) as exc:
        raise ValidationError("The template file is unavailable.") from exc
    if (
        template.sha256 != inspected.sha256
        or template.size_bytes != len(inspected.content)
        or template.content_type != inspected.content_type
    ):
        raise ValidationError("The template metadata does not match its private file.")
    template.approved_by = employee
    template.approved_at = timezone.now()
    template.save(update_fields=("approved_by", "approved_at"))
    return template


@transaction.atomic
def approve_document_requirement(*, user, requirement):
    requirement = (
        ErasmusDocumentRequirement.all_objects.select_for_update(of=("self",))
        .select_related("company", "approved_template")
        .get(pk=requirement.pk)
    )
    employee = _require_company_permission(
        user, requirement.company, "tvg_erasmus.approve_document_requirement"
    )
    if requirement.approved_at:
        raise ValidationError("This document requirement is already approved.")
    requirement.full_clean()
    requirement.approved_by = employee
    requirement.approved_at = timezone.now()
    requirement.save(update_fields=("approved_by", "approved_at"))
    return requirement


def _event(
    document,
    event_type,
    actor_type,
    *,
    employee=None,
    person_public_id=None,
    from_status="",
    to_status="",
    payload=None,
):
    return ErasmusDocumentEvent.all_objects.create(
        document=document,
        event_type=event_type,
        actor_type=actor_type,
        actor_employee=employee,
        actor_person_public_id=person_public_id,
        from_status=from_status,
        to_status=to_status,
        payload=payload or {},
    )


def _matching_requirements(case):
    company_id = case.application.opening.venture.legal_employer_id
    rows = list(
        ErasmusDocumentRequirement.all_objects.select_related("approved_template")
        .filter(
            company_id=company_id,
            approved_at__isnull=False,
            approved_by__isnull=False,
            is_active=True,
        )
        .filter(Q(university="") | Q(university__iexact=case.university))
        .filter(Q(programme="") | Q(programme__iexact=case.programme))
    )
    # A specific approved configuration replaces a wildcard with the same code.
    rows.sort(
        key=lambda item: (
            bool(item.university),
            bool(item.programme),
            item.approved_at,
            item.version_number,
            item.pk,
        ),
        reverse=True,
    )
    selected = {}
    for item in rows:
        selected.setdefault(item.code, item)
    return list(selected.values())


def _frozen_requirements(case):
    identifiers = list(case.document_requirement_snapshot or [])
    if not identifiers:
        return []
    rows = {
        str(item.public_id): item
        for item in ErasmusDocumentRequirement.all_objects.select_related(
            "approved_template"
        ).filter(public_id__in=identifiers)
    }
    if len(rows) != len(identifiers):
        raise ValidationError(
            "A frozen document requirement is unavailable; restore it before continuing."
        )
    return [rows[value] for value in identifiers if value in rows]


def _materialize(case, requirements):
    documents = []
    for requirement in requirements:
        document, created = ErasmusCaseDocument.all_objects.get_or_create(
            case=case,
            requirement=requirement,
            amendment_of=None,
            defaults={
                "template": requirement.approved_template,
                "code": requirement.code,
                "title": requirement.title,
                "document_type": requirement.document_type,
                "lifecycle_phase": requirement.lifecycle_phase,
                "required": requirement.required,
                "required_signer_roles": list(requirement.signer_roles),
                "instructions": requirement.instructions,
                "external_system": requirement.external_system,
                "external_requirement_code": requirement.external_requirement_code,
            },
        )
        if created:
            _event(
                document,
                "erasmus.document_requested",
                ErasmusDocumentEvent.ActorType.SYSTEM,
                to_status=document.status,
                payload={"required": document.required},
            )
        documents.append(document)
    return documents


@transaction.atomic
def initialize_document_lifecycle(*, user, case):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related("application__opening__venture__legal_employer")
        .get(pk=case.pk)
    )
    _require_case_access(user, case, "tvg_erasmus.manage_case_documents")
    if case.acceptance_status != ErasmusCase.AcceptanceStatus.RECEIVED:
        raise ValidationError(
            "The acceptance letter must be received before documentation starts."
        )
    requirements = _frozen_requirements(case) or _matching_requirements(case)
    learning = [
        item
        for item in requirements
        if item.document_type
        == ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT
        and item.lifecycle_phase == ErasmusDocumentRequirement.Phase.BEFORE_MOBILITY
    ]
    if not learning:
        raise ValidationError(
            "Configure and approve a Before Mobility Learning Agreement requirement."
        )
    if not case.document_requirement_snapshot:
        case.document_requirement_snapshot = [
            str(item.public_id) for item in requirements
        ]
        case.save(update_fields=("document_requirement_snapshot",))
    return _materialize(case, learning)


@transaction.atomic
def initialize_after_internship_documents(*, user, case):
    """Materialize approved frozen After Internship requirements for offboarding."""
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related("application__opening__venture__legal_employer")
        .get(pk=case.pk)
    )
    _require_case_access(user, case, "tvg_erasmus.manage_case_documents")
    if case.acceptance_status != ErasmusCase.AcceptanceStatus.RECEIVED:
        raise ValidationError(
            "The acceptance letter must be received before completion documents start."
        )
    requirements = _frozen_requirements(case) or _matching_requirements(case)
    if not case.document_requirement_snapshot:
        case.document_requirement_snapshot = [
            str(item.public_id) for item in requirements
        ]
        case.save(update_fields=("document_requirement_snapshot",))
    after_internship = [
        item
        for item in requirements
        if item.lifecycle_phase
        == ErasmusDocumentRequirement.Phase.AFTER_INTERNSHIP
    ]
    return _materialize(case, after_internship)


def _owned_candidate_document(*, document_id, person_public_id, lock=False):
    queryset = ErasmusCaseDocument.all_objects.select_related(
        "case__application__person",
        "case__application__candidate",
        "case__application__opening__venture__legal_employer",
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    document = queryset.filter(
        public_id=document_id,
        case__application__person__public_id=person_public_id,
        case__acceptance_status=ErasmusCase.AcceptanceStatus.RECEIVED,
        is_active=True,
    ).first()
    if not document:
        raise PermissionDenied
    return document


def _inspect(upload):
    return inspect_candidate_document(
        upload,
        max_bytes=10 * 1024 * 1024,
        allowed_extensions=(".pdf", ".docx"),
    )


def _create_version(
    *,
    document,
    inspected,
    filename,
    source,
    employee=None,
    person_public_id=None,
):
    latest = document.versions.order_by("-version_number").first()
    version = ErasmusDocumentVersion(
        document=document,
        version_number=(latest.version_number + 1 if latest else 1),
        original_filename=Path(os.path.basename(filename)).name[:180],
        content_type=inspected.content_type,
        sha256=inspected.sha256,
        size_bytes=len(inspected.content),
        source=source,
        uploaded_by=employee,
        uploaded_by_person_public_id=person_public_id,
    )
    version.file.save(
        f"document{inspected.extension}", ContentFile(inspected.content), save=False
    )
    version.save()
    return version


@transaction.atomic
def candidate_submit_document(*, document_id, person_public_id, upload):
    document = _owned_candidate_document(
        document_id=document_id, person_public_id=person_public_id, lock=True
    )
    if document.status not in {
        ErasmusCaseDocument.Status.REQUESTED,
        ErasmusCaseDocument.Status.REVISION_NEEDED,
    }:
        raise ValidationError("This document is not accepting a candidate upload.")
    inspected = _inspect(upload)
    previous = document.status
    version = _create_version(
        document=document,
        inspected=inspected,
        filename=upload.name,
        source=ErasmusDocumentVersion.Source.CANDIDATE_UPLOAD,
        person_public_id=person_public_id,
    )
    document.status = ErasmusCaseDocument.Status.SUBMITTED
    document.revision_reason = ""
    document.reviewed_by = None
    document.reviewed_at = None
    document.approved_by = None
    document.approved_at = None
    document.save(
        update_fields=(
            "status",
            "revision_reason",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "approved_at",
        )
    )
    _event(
        document,
        "erasmus.document_submitted",
        ErasmusDocumentEvent.ActorType.CANDIDATE,
        person_public_id=person_public_id,
        from_status=previous,
        to_status=document.status,
        payload={"version": version.version_number},
    )
    return version


@transaction.atomic
def staff_upload_document(*, user, document, upload):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__application__candidate__stage_id",
            "case__application__opening__recruitment",
            "case__application__opening__venture",
        )
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.manage_case_documents"
    )
    if document.status in {
        ErasmusCaseDocument.Status.SIGNED,
        ErasmusCaseDocument.Status.RETURNED,
    }:
        raise ValidationError(
            "Use a Learning Agreement amendment for signed documents."
        )
    inspected = _inspect(upload)
    previous = document.status
    version = _create_version(
        document=document,
        inspected=inspected,
        filename=upload.name,
        source=ErasmusDocumentVersion.Source.STAFF_UPLOAD,
        employee=employee,
    )
    document.status = ErasmusCaseDocument.Status.REVIEWED
    document.reviewed_by = employee
    document.reviewed_at = timezone.now()
    document.revision_reason = ""
    document.approved_by = None
    document.approved_at = None
    document.save()
    _event(
        document,
        "erasmus.document_staff_version_uploaded",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
        payload={"version": version.version_number},
    )
    return version


@transaction.atomic
def generate_from_approved_template(*, user, document):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("template", "case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.manage_case_documents"
    )
    template = document.template
    if not template or not template.approved_at or not template.approved_by_id:
        raise ValidationError("An approved template is required.")
    if (
        template.company_id
        != document.case.application.opening.venture.legal_employer_id
    ):
        raise ValidationError("The approved template belongs to another employer.")
    if document.status not in {
        ErasmusCaseDocument.Status.REQUESTED,
        ErasmusCaseDocument.Status.REVISION_NEEDED,
    }:
        raise ValidationError("A template copy cannot be generated in this state.")
    try:
        with template.source_file.open("rb") as source:
            inspected = _inspect(source)
    except (FileNotFoundError, OSError) as exc:
        raise ValidationError("The approved template file is unavailable.") from exc
    previous = document.status
    version = _create_version(
        document=document,
        inspected=inspected,
        filename=template.source_file.name,
        source=ErasmusDocumentVersion.Source.TEMPLATE_COPY,
        employee=employee,
    )
    document.status = ErasmusCaseDocument.Status.SUBMITTED
    document.revision_reason = ""
    document.save(update_fields=("status", "revision_reason"))
    _event(
        document,
        "erasmus.approved_template_copied",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
        payload={
            "version": version.version_number,
            "template_id": str(template.public_id),
            "template_version": template.version_number,
        },
    )
    return version


@transaction.atomic
def review_document(*, user, document, revision_reason=""):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.review_case_documents"
    )
    if document.status != ErasmusCaseDocument.Status.SUBMITTED:
        raise ValidationError("Only a submitted document can be reviewed.")
    previous = document.status
    if revision_reason.strip():
        document.status = ErasmusCaseDocument.Status.REVISION_NEEDED
        document.revision_reason = revision_reason.strip()[:500]
        event_type = "erasmus.document_revision_requested"
    else:
        document.status = ErasmusCaseDocument.Status.REVIEWED
        document.revision_reason = ""
        event_type = "erasmus.document_reviewed"
    document.reviewed_by = employee
    document.reviewed_at = timezone.now()
    document.save()
    _event(
        document,
        event_type,
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
    )
    return document


@transaction.atomic
def approve_document(*, user, document):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.approve_case_documents"
    )
    if document.status != ErasmusCaseDocument.Status.REVIEWED:
        raise ValidationError("Human review is required before approval.")
    if not document.versions.exists():
        raise ValidationError("A document version is required before approval.")
    previous = document.status
    document.status = ErasmusCaseDocument.Status.APPROVED
    document.approved_by = employee
    document.approved_at = timezone.now()
    document.save(update_fields=("status", "approved_by", "approved_at"))
    _event(
        document,
        "erasmus.document_approved",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
    )
    _update_before_mobility(document.case, employee)
    return document


def _latest_version(document):
    version = document.versions.order_by("-version_number").first()
    if not version:
        raise ValidationError("A document version is required.")
    return version


@transaction.atomic
def record_signature(
    *, user, document, signer_role, signed_at=None, method=None, version=None
):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.sign_case_documents"
    )
    if document.status not in {
        ErasmusCaseDocument.Status.APPROVED,
        ErasmusCaseDocument.Status.SIGNED,
    }:
        raise ValidationError(
            "The document must be approved before signatures are recorded."
        )
    if signer_role not in document.required_signer_roles:
        raise ValidationError("This signer role is not required for the document.")
    latest = _latest_version(document)
    if version and version.pk != latest.pk:
        raise ValidationError(
            "Signatures may only be recorded against the latest version."
        )
    signature, created = ErasmusDocumentSignature.all_objects.get_or_create(
        document=document,
        version=latest,
        signer_role=signer_role,
        defaults={
            "signed_at": signed_at or timezone.now(),
            "method": method or ErasmusDocumentSignature.Method.MANUAL,
            "recorded_by": employee,
        },
    )
    if not created:
        raise ValidationError(
            "This signature is already recorded for the latest version."
        )
    present = set(latest.signatures.values_list("signer_role", flat=True))
    previous = document.status
    if set(document.required_signer_roles).issubset(present):
        document.status = ErasmusCaseDocument.Status.SIGNED
        document.save(update_fields=("status",))
    _event(
        document,
        "erasmus.document_signature_recorded",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
        payload={
            "signer_role": signer_role,
            "version": latest.version_number,
            "method": signature.method,
        },
    )
    return signature


@transaction.atomic
def upload_manual_signed_document(
    *, user, document, upload, signed_roles, signed_at=None
):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.sign_case_documents"
    )
    if document.status != ErasmusCaseDocument.Status.APPROVED:
        raise ValidationError(
            "The document must be approved before a signed copy is uploaded."
        )
    roles = list(dict.fromkeys(signed_roles))
    if not roles or any(role not in document.required_signer_roles for role in roles):
        raise ValidationError("Choose required signer roles for the signed copy.")
    inspected = _inspect(upload)
    version = _create_version(
        document=document,
        inspected=inspected,
        filename=upload.name,
        source=ErasmusDocumentVersion.Source.MANUAL_SIGNED_UPLOAD,
        employee=employee,
    )
    for role in roles:
        ErasmusDocumentSignature.all_objects.create(
            document=document,
            version=version,
            signer_role=role,
            signed_at=signed_at or timezone.now(),
            method=ErasmusDocumentSignature.Method.MANUAL,
            recorded_by=employee,
        )
    previous = document.status
    if set(document.required_signer_roles).issubset(set(roles)):
        document.status = ErasmusCaseDocument.Status.SIGNED
        document.save(update_fields=("status",))
    _event(
        document,
        "erasmus.manual_signed_document_uploaded",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
        payload={"version": version.version_number, "signer_roles": roles},
    )
    return version


def _is_complete(document):
    if document.required_signer_roles:
        return document.status == ErasmusCaseDocument.Status.RETURNED
    return document.status in {
        ErasmusCaseDocument.Status.APPROVED,
        ErasmusCaseDocument.Status.SIGNED,
        ErasmusCaseDocument.Status.RETURNED,
    }


def _update_before_mobility(case, employee):
    required = list(
        case.documents.filter(
            lifecycle_phase=ErasmusDocumentRequirement.Phase.BEFORE_MOBILITY,
            required=True,
            amendment_of__isnull=True,
        )
    )
    if required and all(_is_complete(document) for document in required):
        if not case.before_mobility_completed_at:
            case.before_mobility_completed_at = timezone.now()
            case.before_mobility_completed_by = employee
            case.save(
                update_fields=(
                    "before_mobility_completed_at",
                    "before_mobility_completed_by",
                )
            )
            _event(
                required[-1],
                "erasmus.before_mobility_completed",
                ErasmusDocumentEvent.ActorType.STAFF,
                employee=employee,
                from_status=required[-1].status,
                to_status=required[-1].status,
            )


@transaction.atomic
def return_signed_document(*, user, document):
    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.return_case_documents"
    )
    if document.status != ErasmusCaseDocument.Status.SIGNED:
        raise ValidationError("All required signatures must be recorded before return.")
    previous = document.status
    document.status = ErasmusCaseDocument.Status.RETURNED
    document.returned_by = employee
    document.returned_at = timezone.now()
    document.save(update_fields=("status", "returned_by", "returned_at"))
    _event(
        document,
        "erasmus.signed_document_returned",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=previous,
        to_status=document.status,
    )
    if (
        document.document_type
        == ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT
        and document.lifecycle_phase == ErasmusDocumentRequirement.Phase.BEFORE_MOBILITY
    ):
        remaining = [
            item
            for item in _frozen_requirements(document.case)
            if item.document_type
            != ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT
        ]
        _materialize(document.case, remaining)
    _update_before_mobility(document.case, employee)
    return document


@transaction.atomic
def create_learning_agreement_amendment(*, user, document):
    original = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, original, "tvg_erasmus.manage_case_documents"
    )
    if (
        original.document_type
        != ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT
        or original.status
        not in {ErasmusCaseDocument.Status.SIGNED, ErasmusCaseDocument.Status.RETURNED}
    ):
        raise ValidationError("Only a signed Learning Agreement can be amended.")
    amendment = ErasmusCaseDocument.all_objects.create(
        case=original.case,
        requirement=original.requirement,
        template=original.template,
        amendment_of=original,
        code=f"{original.code}-amendment-{original.amendments.count() + 1}",
        title=f"{original.title} amendment {original.amendments.count() + 1}",
        document_type=original.document_type,
        lifecycle_phase=ErasmusDocumentRequirement.Phase.DURING_MOBILITY,
        required=True,
        required_signer_roles=list(original.required_signer_roles),
        instructions=original.instructions,
        external_system=original.external_system,
        external_requirement_code=original.external_requirement_code,
    )
    _event(
        amendment,
        "erasmus.learning_agreement_amendment_requested",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        to_status=amendment.status,
        payload={"amends_document_id": str(original.public_id)},
    )
    return amendment


@transaction.atomic
def request_signing_service(*, user, document, provider_key="self_hosted"):
    from tvg_erasmus.signing import get_signing_provider

    document = (
        ErasmusCaseDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__application__opening__venture")
        .get(pk=document.pk)
    )
    employee = _require_document_access(
        user, document, "tvg_erasmus.sign_case_documents"
    )
    if document.status != ErasmusCaseDocument.Status.APPROVED:
        raise ValidationError(
            "The document must be approved before signing is requested."
        )
    if not document.required_signer_roles:
        raise ValidationError(
            "Configure required signer roles before requesting signing."
        )
    version = _latest_version(document)
    key = (
        f"erasmus-signing:{document.public_id}:{version.version_number}:{provider_key}"
    )
    existing = ErasmusSigningRequest.all_objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    provider = get_signing_provider(provider_key)
    reference = provider.request_signature(
        document=document,
        version=version,
        signer_roles=tuple(document.required_signer_roles),
        idempotency_key=key,
    )
    request = ErasmusSigningRequest.all_objects.create(
        document=document,
        version=version,
        provider_key=provider_key,
        provider_reference=reference,
        idempotency_key=key,
        requested_by=employee,
    )
    _event(
        document,
        "erasmus.signing_service_requested",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        from_status=document.status,
        to_status=document.status,
        payload={"provider": provider_key, "version": version.version_number},
    )
    return request


@transaction.atomic
def record_accommodation_response(*, case_id, person_public_id, needs_assistance):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related("application__person")
        .filter(
            public_id=case_id,
            application__person__public_id=person_public_id,
            acceptance_status=ErasmusCase.AcceptanceStatus.RECEIVED,
        )
        .first()
    )
    if not case:
        raise PermissionDenied
    agreement = (
        case.documents.filter(
            document_type=ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT,
            status__in=(
                ErasmusCaseDocument.Status.SIGNED,
                ErasmusCaseDocument.Status.RETURNED,
            ),
        )
        .order_by("-created_at")
        .first()
    )
    if not agreement:
        raise ValidationError("The Learning Agreement must be signed first.")
    request, _created = ErasmusAccommodationRequest.all_objects.update_or_create(
        case=case,
        defaults={
            "needs_assistance": bool(needs_assistance),
            "requested_at": timezone.now(),
            "status": (
                ErasmusAccommodationRequest.Status.PENDING
                if needs_assistance
                else ErasmusAccommodationRequest.Status.CLOSED
            ),
            "handled_by": None,
            "handled_at": None,
        },
    )
    _event(
        agreement,
        "erasmus.accommodation_response_recorded",
        ErasmusDocumentEvent.ActorType.CANDIDATE,
        person_public_id=person_public_id,
        payload={"assistance_requested": bool(needs_assistance)},
    )
    return request


@transaction.atomic
def update_accommodation_follow_up(*, user, case, status):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related("application__opening__venture")
        .get(pk=case.pk)
    )
    employee = _require_case_access(user, case, "tvg_erasmus.manage_case_documents")
    request = (
        ErasmusAccommodationRequest.all_objects.select_for_update(of=("self",))
        .filter(case=case, needs_assistance=True)
        .first()
    )
    if not request:
        raise ValidationError("There is no accommodation follow-up request.")
    if status not in {
        ErasmusAccommodationRequest.Status.CONTACTED,
        ErasmusAccommodationRequest.Status.CLOSED,
    }:
        raise ValidationError("Choose a valid follow-up status.")
    request.status = status
    request.handled_by = employee
    request.handled_at = timezone.now()
    request.save(update_fields=("status", "handled_by", "handled_at"))
    agreement = (
        case.documents.filter(
            document_type=ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT,
            status__in=(
                ErasmusCaseDocument.Status.SIGNED,
                ErasmusCaseDocument.Status.RETURNED,
            ),
        )
        .order_by("-created_at")
        .first()
    )
    if agreement:
        _event(
            agreement,
            "erasmus.accommodation_follow_up_updated",
            ErasmusDocumentEvent.ActorType.STAFF,
            employee=employee,
            payload={"status": status},
        )
    return request


def _remaining_roles(document, latest):
    if not latest:
        return list(document.required_signer_roles)
    present = set(latest.signatures.values_list("signer_role", flat=True))
    return [role for role in document.required_signer_roles if role not in present]


def _candidate_visible_versions(document, person_public_id):
    versions = list(document.versions.all())
    visible = [
        version
        for version in versions
        if version.source == ErasmusDocumentVersion.Source.CANDIDATE_UPLOAD
        and version.uploaded_by_person_public_id == person_public_id
    ]
    if document.status == ErasmusCaseDocument.Status.RETURNED and versions:
        latest = max(versions, key=lambda version: version.version_number)
        if latest not in visible:
            visible.append(latest)
    return sorted(visible, key=lambda version: version.version_number, reverse=True)


def document_projection(application):
    case = (
        ErasmusCase.all_objects.filter(application=application, is_active=True)
        .prefetch_related("documents__versions__signatures")
        .first()
    )
    if not case or case.acceptance_status != ErasmusCase.AcceptanceStatus.RECEIVED:
        return None
    documents = []
    learning_signed = False
    person_public_id = application.person.public_id
    for document in case.documents.all():
        latest = document.versions.order_by("-version_number").first()
        visible_versions = _candidate_visible_versions(document, person_public_id)
        learning_signed = learning_signed or (
            document.document_type
            == ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT
            and document.status
            in {
                ErasmusCaseDocument.Status.SIGNED,
                ErasmusCaseDocument.Status.RETURNED,
            }
        )
        documents.append(
            {
                "id": str(document.public_id),
                "code": document.code,
                "title": document.title,
                "document_type": document.document_type,
                "phase": document.lifecycle_phase,
                "required": document.required,
                "status": document.status,
                "status_label": document.get_status_display(),
                "instructions": document.instructions,
                "revision_reason": document.revision_reason,
                "can_upload": document.status
                in {
                    ErasmusCaseDocument.Status.REQUESTED,
                    ErasmusCaseDocument.Status.REVISION_NEEDED,
                },
                "latest_version": latest.version_number if latest else None,
                "latest_version_id": str(latest.public_id) if latest else None,
                "versions": [
                    {
                        "id": str(version.public_id),
                        "number": version.version_number,
                        "filename": version.original_filename,
                        "content_type": version.content_type,
                        "source": version.source,
                        "created_at": version.created_at.isoformat(),
                    }
                    for version in visible_versions
                ],
                "can_download": bool(visible_versions),
                "remaining_signer_roles": _remaining_roles(document, latest),
            }
        )
    response = getattr(case, "accommodation_request", None)
    return {
        "case_id": str(case.public_id),
        "documents": documents,
        "before_mobility_complete": bool(case.before_mobility_completed_at),
        "accommodation_question": ACCOMMODATION_QUESTION if learning_signed else "",
        "accommodation_response": (
            response.needs_assistance if response is not None else None
        ),
    }


def candidate_document_version(*, document_id, version_id, person_public_id):
    document = _owned_candidate_document(
        document_id=document_id, person_public_id=person_public_id
    )
    version = next(
        (
            item
            for item in _candidate_visible_versions(document, person_public_id)
            if item.public_id == version_id
        ),
        None,
    )
    if not version:
        raise PermissionDenied
    _event(
        document,
        "erasmus.document_downloaded",
        ErasmusDocumentEvent.ActorType.CANDIDATE,
        person_public_id=person_public_id,
        payload={"version": version.version_number},
    )
    return version


def staff_document_version(*, user, version):
    employee = _require_document_access(
        user, version.document, "tvg_erasmus.download_case_documents"
    )
    _event(
        version.document,
        "erasmus.document_downloaded",
        ErasmusDocumentEvent.ActorType.STAFF,
        employee=employee,
        payload={"version": version.version_number},
    )
    return version


def visible_case_documents(user):
    case_ids = visible_erasmus_cases(user).values("pk")
    if not user.has_perm("tvg_erasmus.view_erasmuscase"):
        return ErasmusCaseDocument.all_objects.none()
    return ErasmusCaseDocument.all_objects.filter(case_id__in=case_ids)
