from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from asset.models import AssetAssignment, AssetServiceRequest
from base.auth_backends import get_allowed_company_ids
from employee.models import Employee
from notifications.signals import notify
from offboarding.models import OffboardingEmployee
from tvg_careers.document_security import inspect_candidate_document
from tvg_erasmus.document_services import initialize_after_internship_documents
from tvg_erasmus.models import ErasmusCaseDocument, ErasmusDocumentRequirement
from tvg_onboarding.models import InternOnboarding
from tvg_peoplehub.services import company_id_for, employee_for_user
from tvg_reporting.models import (
    MonthlyPresentation,
    ReportingRubricVersion,
    ReportStatus,
    ReviewOutcome,
    WeeklyReport,
)

from .documents import render_completion_document
from .integrations import get_access_revocation_provider
from .models import (
    AccessRevocationException,
    AccessRevocationTask,
    CompletionCase,
    CompletionDocument,
    CompletionDocumentSignature,
    CompletionDocumentTemplate,
    CompletionEvent,
    CompletionException,
    CompletionNotification,
    CompletionPolicy,
    CompletionRequirement,
    FinalSupervisorFeedback,
    ManualAccessRevocationTask,
    ReferenceLetterRequest,
    RetentionPolicy,
    RetentionSchedule,
)


def _authorize(user, permission, *, case=None, onboarding=None):
    actor = employee_for_user(user)
    company = (
        case.onboarding.venture.legal_employer
        if case
        else onboarding.venture.legal_employer
    )
    if actor.get_company() != company:
        raise PermissionDenied("The completion case belongs to another company.")
    if not user.is_superuser and (
        company.pk not in get_allowed_company_ids(user) or not user.has_perm(permission)
    ):
        raise PermissionDenied("The required completion permission is not assigned.")
    return actor


def can_view_case(user, viewer, case):
    if company_id_for(viewer) != case.onboarding.venture.legal_employer_id:
        return False
    return bool(
        viewer.pk == case.employee_id
        or viewer.pk == case.onboarding.supervisor_id
        or user.is_superuser
        or user.has_perm("tvg_offboarding.manage_intern_completion")
        or user.has_perm("tvg_offboarding.view_completion_management")
    )


def _event(case, event_type, actor=None, *, obj=None, metadata=None):
    return CompletionEvent.all_objects.create(
        case=case,
        event_type=event_type,
        actor=actor,
        object_type=type(obj).__name__ if obj else "",
        object_reference=(
            str(getattr(obj, "public_id", getattr(obj, "pk", ""))) if obj else ""
        ),
        metadata=metadata or {},
    )


def _schedule_notification(*, case, recipient, kind, suffix, scheduled_for=None):
    return CompletionNotification.all_objects.get_or_create(
        dedupe_key=f"{kind}:{case.public_id}:{recipient.pk}:{suffix}",
        defaults={
            "case": case,
            "recipient": recipient,
            "kind": kind,
            "scheduled_for": scheduled_for or timezone.now(),
        },
    )[0]


def _completion_recipients(case):
    recipients = {case.onboarding.supervisor_id: case.onboarding.supervisor}
    policy_id = case.policy_snapshot.get("policy_id")
    policy = CompletionPolicy.all_objects.filter(pk=policy_id).first()
    if policy:
        recipients.update(
            {employee.pk: employee for employee in policy.hr_recipients.all()}
        )
    return recipients.values()


def _snapshot(policy, *, had_assets):
    return {
        "policy_id": policy.pk,
        "start_days_before_end": policy.start_days_before_end,
        "require_final_weekly_report": policy.require_final_weekly_report,
        "require_final_monthly_presentation": policy.require_final_monthly_presentation,
        "require_final_presentation_review": policy.require_final_presentation_review,
        "require_asset_return_document": policy.require_asset_return_document,
        "access_systems": list(policy.access_systems),
        "had_assets": had_assets,
    }


def _requirement_defaults(case):
    snapshot = case.policy_snapshot
    return (
        (
            CompletionRequirement.Code.FINAL_WEEKLY_REPORT,
            "Final weekly report",
            snapshot["require_final_weekly_report"],
        ),
        (
            CompletionRequirement.Code.FINAL_MONTHLY_PRESENTATION,
            "Final monthly presentation",
            snapshot["require_final_monthly_presentation"],
        ),
        (
            CompletionRequirement.Code.FINAL_PRESENTATION_REVIEW,
            "Final presentation and review",
            snapshot["require_final_presentation_review"],
        ),
        (
            CompletionRequirement.Code.FINAL_SUPERVISOR_FEEDBACK,
            "Final supervisor feedback",
            True,
        ),
        (
            CompletionRequirement.Code.COMPLETION_CERTIFICATE,
            "Internship completion certificate",
            True,
        ),
        (
            CompletionRequirement.Code.UNIVERSITY_DOCUMENTS,
            "University After Mobility documents",
            False,
        ),
        (
            CompletionRequirement.Code.ASSET_RETURN,
            "Company assets returned",
            True,
        ),
        (
            CompletionRequirement.Code.ASSET_RETURN_DOCUMENT,
            "Signed asset handover/return document",
            bool(snapshot["had_assets"] and snapshot["require_asset_return_document"]),
        ),
        (
            CompletionRequirement.Code.ACCESS_REVOCATION,
            "Company access revoked",
            bool(snapshot["access_systems"]),
        ),
        (
            CompletionRequirement.Code.ADMINISTRATIVE_CLOSURE,
            "Final administrative closure confirmed",
            True,
        ),
    )


def _set_requirement(
    case,
    code,
    *,
    completed,
    actor=None,
    source_type="",
    source_reference="",
    required=None,
):
    requirement = CompletionRequirement.all_objects.get(case=case, code=code)
    if required is not None and requirement.required != required:
        requirement.required = required
    if requirement.status != CompletionRequirement.Status.EXCEPTED:
        target = (
            CompletionRequirement.Status.COMPLETED
            if completed or not requirement.required
            else CompletionRequirement.Status.PENDING
        )
        if requirement.status != target:
            requirement.status = target
            requirement.completed_by = (
                actor if target == CompletionRequirement.Status.COMPLETED else None
            )
            requirement.completed_at = (
                timezone.now()
                if target == CompletionRequirement.Status.COMPLETED
                else None
            )
    if completed:
        requirement.source_type = source_type
        requirement.source_reference = str(source_reference)
    requirement.save()
    return requirement


def _create_case(*, onboarding, policy, actor=None):
    if (
        onboarding.status != InternOnboarding.Status.ONBOARDED
        or not onboarding.employee_id
    ):
        raise ValidationError(
            "Only an onboarded intern with an employee record can complete an internship."
        )
    company = onboarding.venture.legal_employer
    if policy.company_id != company.pk:
        raise ValidationError("Completion policy belongs to another company.")
    existing_native = OffboardingEmployee._base_manager.filter(
        employee_id=onboarding.employee
    ).first()
    if existing_native:
        if existing_native.stage_id.offboarding_id_id != policy.native_offboarding_id:
            raise ValidationError(
                "The employee is already in a different native offboarding process."
            )
        native_employee = existing_native
    else:
        first_stage = policy.native_offboarding.offboardingstage_set.order_by(
            "sequence", "created_at", "pk"
        ).first()
        if not first_stage:
            raise ValidationError(
                "The configured native offboarding process has no stages."
            )
        native_employee = OffboardingEmployee._base_manager.create(
            employee_id=onboarding.employee,
            stage_id=first_stage,
            notice_period_starts=timezone.localdate(),
            notice_period_ends=onboarding.end_date,
        )
    outstanding_assets = AssetAssignment._base_manager.filter(
        assigned_to_employee_id=onboarding.employee,
        return_date__isnull=True,
    ).exists()
    case = CompletionCase.all_objects.create(
        onboarding=onboarding,
        employee=onboarding.employee,
        native_offboarding_employee=native_employee,
        internship_end_date=onboarding.end_date,
        policy_snapshot=_snapshot(policy, had_assets=outstanding_assets),
    )
    for code, title, required in _requirement_defaults(case):
        CompletionRequirement.all_objects.create(
            case=case,
            code=code,
            title=title,
            required=required,
            status=(
                CompletionRequirement.Status.PENDING
                if required
                else CompletionRequirement.Status.COMPLETED
            ),
            completed_at=timezone.now() if not required else None,
        )
    hr_recipients = list(policy.hr_recipients.select_related("employee_work_info"))
    if any(item.get_company() != company for item in hr_recipients):
        raise ValidationError(
            "Every HR notification recipient must belong to the company."
        )
    recipients = {onboarding.supervisor_id: onboarding.supervisor}
    recipients.update({item.pk: item for item in hr_recipients})
    for recipient in recipients.values():
        _schedule_notification(
            case=case,
            recipient=recipient,
            kind=CompletionNotification.Kind.UPCOMING_END,
            suffix="created",
        )
    _event(case, "completion.upcoming_created", actor)
    refresh_completion_state(case=case, actor=actor)
    return case


@transaction.atomic
def start_completion(*, user, onboarding):
    onboarding = (
        InternOnboarding.all_objects.select_for_update(of=("self",))
        .select_related(
            "employee__employee_work_info",
            "venture__legal_employer",
            "supervisor",
            "erasmus_case",
        )
        .get(pk=onboarding.pk)
    )
    actor = _authorize(
        user, "tvg_offboarding.start_intern_completion", onboarding=onboarding
    )
    policy = CompletionPolicy.all_objects.select_related("native_offboarding").get(
        company=onboarding.venture.legal_employer
    )
    case = CompletionCase.all_objects.filter(onboarding=onboarding).first()
    if not case:
        case = _create_case(onboarding=onboarding, policy=policy, actor=actor)
    case = CompletionCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
    if case.state == CompletionCase.State.COMPLETED:
        raise ValidationError("This internship completion is already finalized.")
    if not case.started_at:
        case.started_by = actor
        case.started_at = timezone.now()
        case.state = CompletionCase.State.IN_PROGRESS
        case.save(update_fields=("started_by", "started_at", "state"))
        _event(case, "completion.started", actor)
    documents = initialize_after_internship_documents(
        user=user, case=onboarding.erasmus_case
    )
    _sync_university_documents(case, documents=documents, actor=actor)
    for row in case.policy_snapshot["access_systems"]:
        AccessRevocationTask.all_objects.get_or_create(
            case=case,
            system_key=row["key"],
            defaults={
                "system_label": row["label"],
                "provider": row["provider"],
                "requested_by": actor,
            },
        )
    _schedule_notification(
        case=case,
        recipient=case.employee,
        kind=CompletionNotification.Kind.FINAL_REPORTING,
        suffix="start",
    )
    _schedule_notification(
        case=case,
        recipient=case.onboarding.supervisor,
        kind=CompletionNotification.Kind.REVIEW_REQUIRED,
        suffix="start",
    )
    return refresh_completion_state(case=case, actor=actor)


def materialize_upcoming_completions(*, as_of=None):
    as_of = as_of or timezone.localdate()
    created = []
    for policy in CompletionPolicy.all_objects.select_related(
        "company", "native_offboarding"
    ).prefetch_related("hr_recipients"):
        cutoff = as_of + timedelta(days=policy.start_days_before_end)
        onboardings = (
            InternOnboarding.all_objects.select_related(
                "employee", "supervisor", "venture__legal_employer"
            )
            .filter(
                venture__legal_employer=policy.company,
                status=InternOnboarding.Status.ONBOARDED,
                employee__isnull=False,
                end_date__lte=cutoff,
                completion_case__isnull=True,
            )
            .order_by("end_date", "pk")
        )
        for onboarding in onboardings:
            with transaction.atomic():
                locked = InternOnboarding.all_objects.select_for_update(of=("self",)).get(
                    pk=onboarding.pk
                )
                if not CompletionCase.all_objects.filter(onboarding=locked).exists():
                    created.append(
                        _create_case(onboarding=locked, policy=policy, actor=None)
                    )
    return created


def _document_complete(document):
    if document.required_signer_roles:
        return document.status == ErasmusCaseDocument.Status.RETURNED
    return document.status in {
        ErasmusCaseDocument.Status.APPROVED,
        ErasmusCaseDocument.Status.SIGNED,
        ErasmusCaseDocument.Status.RETURNED,
    }


def _sync_university_documents(case, *, documents=None, actor=None):
    documents = list(
        documents
        if documents is not None
        else case.onboarding.erasmus_case.documents.filter(
            lifecycle_phase=ErasmusDocumentRequirement.Phase.AFTER_INTERNSHIP,
            amendment_of__isnull=True,
        )
    )
    required = [document for document in documents if document.required]
    complete = all(_document_complete(document) for document in required)
    requirement = _set_requirement(
        case,
        CompletionRequirement.Code.UNIVERSITY_DOCUMENTS,
        required=bool(required),
        completed=complete,
        actor=actor,
        source_type="ErasmusCaseDocument",
        source_reference=",".join(str(item.public_id) for item in required),
    )
    for document in documents:
        _ensure_retention_schedule(
            case=case,
            record_kind=RetentionPolicy.RecordKind.UNIVERSITY_DOCUMENT,
            object_public_id=document.public_id,
            required=False,
        )
    return requirement


def _outstanding_assets(case):
    return AssetAssignment._base_manager.filter(
        assigned_to_employee_id=case.employee,
        return_date__isnull=True,
    )


@transaction.atomic
def refresh_completion_state(*, case, actor=None):
    case = (
        CompletionCase.all_objects.select_for_update(of=("self",))
        .select_related("onboarding__erasmus_case", "employee")
        .get(pk=case.pk)
    )
    if case.state == CompletionCase.State.COMPLETED:
        return case
    _sync_university_documents(case, actor=actor)
    _set_requirement(
        case,
        CompletionRequirement.Code.ASSET_RETURN,
        completed=not _outstanding_assets(case).exists(),
        actor=actor,
        source_type="AssetAssignment",
        source_reference="all-returned",
    )
    access_tasks = list(case.access_revocations.all())
    access_complete = bool(access_tasks) and all(
        item.status
        in {AccessRevocationTask.Status.REVOKED, AccessRevocationTask.Status.EXCEPTED}
        for item in access_tasks
    )
    if not case.policy_snapshot.get("access_systems"):
        access_complete = True
    _set_requirement(
        case,
        CompletionRequirement.Code.ACCESS_REVOCATION,
        completed=access_complete,
        actor=actor,
        source_type="AccessRevocationTask",
        source_reference="all-resolved" if access_complete else "",
    )
    requirements = {
        item.code: item for item in CompletionRequirement.all_objects.filter(case=case)
    }
    if not case.started_at:
        target = CompletionCase.State.UPCOMING
    elif (
        requirements[CompletionRequirement.Code.ASSET_RETURN].status
        == CompletionRequirement.Status.PENDING
    ):
        target = CompletionCase.State.AWAITING_ASSETS
    elif (
        requirements[CompletionRequirement.Code.ACCESS_REVOCATION].status
        == CompletionRequirement.Status.PENDING
    ):
        target = CompletionCase.State.AWAITING_ACCESS_REVOCATION
    elif any(
        requirements[code].status == CompletionRequirement.Status.PENDING
        for code in (
            CompletionRequirement.Code.COMPLETION_CERTIFICATE,
            CompletionRequirement.Code.UNIVERSITY_DOCUMENTS,
            CompletionRequirement.Code.ASSET_RETURN_DOCUMENT,
        )
        if requirements[code].required
    ):
        target = CompletionCase.State.AWAITING_DOCUMENTS
    elif case.started_at:
        target = CompletionCase.State.IN_PROGRESS
    else:
        target = CompletionCase.State.UPCOMING
    if case.state != target:
        previous = case.state
        case.state = target
        case.save(update_fields=("state",))
        _event(
            case,
            "completion.state_changed",
            actor,
            metadata={"from": previous, "to": target},
        )
        notification_kind = {
            CompletionCase.State.AWAITING_ASSETS: CompletionNotification.Kind.ASSETS_OUTSTANDING,
            CompletionCase.State.AWAITING_ACCESS_REVOCATION: CompletionNotification.Kind.ACCESS_REVOCATION,
        }.get(target)
        if notification_kind:
            for recipient in _completion_recipients(case):
                _schedule_notification(
                    case=case,
                    recipient=recipient,
                    kind=notification_kind,
                    suffix=target,
                )
    return case


@transaction.atomic
def schedule_final_presentation(*, user, case, scheduled_for):
    actor = _authorize(user, "tvg_offboarding.manage_intern_completion", case=case)
    if timezone.is_naive(scheduled_for):
        scheduled_for = timezone.make_aware(scheduled_for)
    case = CompletionCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
    if case.state == CompletionCase.State.COMPLETED:
        raise ValidationError("Completed cases cannot be rescheduled.")
    case.final_presentation_scheduled_for = scheduled_for
    case.save(update_fields=("final_presentation_scheduled_for",))
    for recipient in (case.employee, case.onboarding.supervisor):
        _schedule_notification(
            case=case,
            recipient=recipient,
            kind=CompletionNotification.Kind.FINAL_PRESENTATION,
            suffix=scheduled_for.isoformat(),
            scheduled_for=timezone.now(),
        )
    _event(
        case,
        "completion.final_presentation_scheduled",
        actor,
        metadata={"scheduled_for": scheduled_for.isoformat()},
    )
    return case


@transaction.atomic
def link_final_weekly_report(*, user, case, report):
    actor = _authorize(user, "tvg_offboarding.manage_intern_completion", case=case)
    report = WeeklyReport.all_objects.get(pk=report.pk)
    if report.employee_id != case.employee_id or report.status != ReportStatus.APPROVED:
        raise ValidationError("Use an approved weekly report belonging to this intern.")
    if not (
        report.reporting_week
        <= case.internship_end_date
        <= report.reporting_week + timedelta(days=6)
    ):
        raise ValidationError("The weekly report must cover the internship end date.")
    _set_requirement(
        case,
        CompletionRequirement.Code.FINAL_WEEKLY_REPORT,
        completed=True,
        actor=actor,
        source_type="WeeklyReport",
        source_reference=report.public_id,
    )
    _event(case, "completion.final_weekly_linked", actor, obj=report)
    return refresh_completion_state(case=case, actor=actor)


@transaction.atomic
def link_final_monthly_presentation(*, user, case, presentation):
    actor = _authorize(user, "tvg_offboarding.manage_intern_completion", case=case)
    case = CompletionCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
    presentation = MonthlyPresentation.all_objects.get(pk=presentation.pk)
    if (
        presentation.employee_id != case.employee_id
        or presentation.status != ReportStatus.APPROVED
    ):
        raise ValidationError("Use an approved presentation belonging to this intern.")
    month = case.internship_end_date.replace(day=1)
    if presentation.presentation_month != month:
        raise ValidationError("The presentation must be for the internship end month.")
    _set_requirement(
        case,
        CompletionRequirement.Code.FINAL_MONTHLY_PRESENTATION,
        completed=True,
        actor=actor,
        source_type="MonthlyPresentation",
        source_reference=presentation.public_id,
    )
    review = presentation.versions.filter(
        version_number=presentation.current_version,
        review__outcome=ReviewOutcome.APPROVED,
    ).first()
    presentation_complete = bool(case.final_presentation_scheduled_for and review)
    _set_requirement(
        case,
        CompletionRequirement.Code.FINAL_PRESENTATION_REVIEW,
        completed=presentation_complete,
        actor=actor,
        source_type="MonthlyPresentationVersion",
        source_reference=getattr(review, "pk", ""),
    )
    _event(case, "completion.final_presentation_linked", actor, obj=presentation)
    return refresh_completion_state(case=case, actor=actor)


def _validate_scores(rubric, scores, adjustments, adjustment_reason, company_id):
    if rubric.company_id != company_id or not rubric.approved_at:
        raise ValidationError("Use an approved rubric version from this company.")
    expected = {item["code"]: item for item in rubric.criteria}
    if set(scores) != set(expected):
        raise ValidationError("Every rubric criterion requires exactly one score.")
    for code, score in scores.items():
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValidationError(f"Rubric score {code} must be numeric.")
        if score < 0 or score > expected[code]["max_score"]:
            raise ValidationError(f"Rubric score {code} is outside its allowed range.")
    if adjustments and not adjustment_reason.strip():
        raise ValidationError("Reviewer adjustments require a reason.")


@transaction.atomic
def record_final_supervisor_feedback(
    *,
    user,
    case,
    comments,
    task_completion_evidence,
    development_feedback,
    writing_quality_comments,
    internship_performance_comments,
    rubric,
    rubric_scores,
    reviewer_adjustments=None,
    adjustment_reason="",
):
    reviewer = employee_for_user(user)
    case = (
        CompletionCase.all_objects.select_for_update(of=("self",))
        .select_related("onboarding__supervisor", "onboarding__venture__legal_employer")
        .get(pk=case.pk)
    )
    if reviewer.pk != case.onboarding.supervisor_id:
        _authorize(user, "tvg_offboarding.manage_intern_completion", case=case)
    elif reviewer.get_company() != case.onboarding.venture.legal_employer:
        raise PermissionDenied("The completion case belongs to another company.")
    if not comments.strip():
        raise ValidationError("Final supervisor comments are required.")
    reviewer_adjustments = reviewer_adjustments or {}
    _validate_scores(
        rubric,
        rubric_scores,
        reviewer_adjustments,
        adjustment_reason,
        case.onboarding.venture.legal_employer_id,
    )
    feedback = FinalSupervisorFeedback.all_objects.create(
        case=case,
        reviewer=reviewer,
        comments=comments,
        task_completion_evidence=task_completion_evidence,
        development_feedback=development_feedback,
        writing_quality_comments=writing_quality_comments,
        internship_performance_comments=internship_performance_comments,
        rubric=rubric,
        rubric_scores=rubric_scores,
        reviewer_adjustments=reviewer_adjustments,
        adjustment_reason=adjustment_reason,
    )
    _set_requirement(
        case,
        CompletionRequirement.Code.FINAL_SUPERVISOR_FEEDBACK,
        completed=True,
        actor=reviewer,
        source_type="FinalSupervisorFeedback",
        source_reference=feedback.pk,
    )
    _event(case, "completion.final_feedback_recorded", reviewer, obj=feedback)
    refresh_completion_state(case=case, actor=reviewer)
    return feedback


@transaction.atomic
def return_asset(
    *, user, case, assignment, return_date, return_status, return_condition
):
    actor = _authorize(user, "tvg_offboarding.manage_completion_assets", case=case)
    if not (user.is_superuser or user.has_perm("asset.change_assetassignment")):
        raise PermissionDenied("Native asset-return permission is required.")
    assignment = (
        AssetAssignment._base_manager.select_for_update(of=("self",))
        .select_related("asset_id", "asset_item_id")
        .get(pk=assignment.pk)
    )
    if assignment.assigned_to_employee_id_id != case.employee_id:
        raise PermissionDenied("This asset is assigned to another employee.")
    if assignment.return_date:
        raise ValidationError("This asset assignment is already returned.")
    if return_date > timezone.localdate():
        raise ValidationError("Asset return date cannot be in the future.")
    if return_status not in dict(AssetAssignment.STATUS):
        raise ValidationError("Choose a valid native asset return status.")
    AssetServiceRequest._base_manager.filter(
        assignment_id=assignment, status__in=["Requested", "In Progress"]
    ).update(status="Returned")
    asset = assignment.asset_id
    assignment.return_date = return_date
    assignment.return_status = return_status
    assignment.return_condition = return_condition
    assignment.return_request = False
    assignment.save()
    if assignment.asset_item_id_id:
        assignment.asset_item_id.status = {
            "Healthy": "Available",
            "Lost": "Lost",
            "Minor damage": "Damaged",
            "Major damage": "Damaged",
        }[return_status]
        assignment.asset_item_id.save(update_fields=("status",))
    active_count = AssetAssignment._base_manager.filter(
        asset_id=asset, return_date__isnull=True
    ).count()
    if return_status == "Healthy":
        asset.asset_status = "Available" if active_count < asset.quantity else "In use"
    else:
        asset.asset_status = (
            "Not-Available" if asset.quantity <= 1 else asset.asset_status
        )
    asset.save(update_fields=("asset_status",))
    _event(
        case,
        "completion.asset_returned",
        actor,
        obj=assignment,
        metadata={"status": return_status},
    )
    refresh_completion_state(case=case, actor=actor)
    return assignment


@transaction.atomic
def attempt_access_revocation(*, user, task):
    task = (
        AccessRevocationTask.all_objects.select_for_update(of=("self",))
        .select_related("case__employee", "case__onboarding__venture__legal_employer")
        .get(pk=task.pk)
    )
    actor = _authorize(user, "tvg_offboarding.manage_access_revocation", case=task.case)
    if task.status in {
        AccessRevocationTask.Status.REVOKED,
        AccessRevocationTask.Status.EXCEPTED,
    }:
        return task
    provider = get_access_revocation_provider(task.provider)
    try:
        result = provider.revoke(task=task, employee=task.case.employee)
    except Exception as exc:
        task.status = AccessRevocationTask.Status.FAILED
        task.last_error = str(exc)[:255]
        task.save(update_fields=("status", "last_error"))
        _event(task.case, "completion.access_revocation_failed", actor, obj=task)
        return task
    if result.supported and result.revoked:
        task.status = AccessRevocationTask.Status.REVOKED
        task.revoked_by = actor
        task.revoked_at = timezone.now()
        task.proof_reference = result.proof_reference
        task.last_error = ""
        task.save(
            update_fields=(
                "status",
                "revoked_by",
                "revoked_at",
                "proof_reference",
                "last_error",
            )
        )
        _event(task.case, "completion.access_revoked", actor, obj=task)
    else:
        task.status = AccessRevocationTask.Status.MANUAL_REQUIRED
        task.last_error = result.message[:255]
        task.save(update_fields=("status", "last_error"))
        ManualAccessRevocationTask.all_objects.get_or_create(
            revocation=task,
            defaults={
                "title": f"Revoke {task.system_label} access",
                "instructions": (
                    "Use the authorized administrative process, then record a "
                    "non-secret proof reference or approved proof document."
                ),
            },
        )
        _event(task.case, "completion.manual_access_task_created", actor, obj=task)
    refresh_completion_state(case=task.case, actor=actor)
    return task


@transaction.atomic
def complete_manual_access_revocation(
    *, user, task, proof_reference="", proof_upload=None
):
    task = (
        AccessRevocationTask.all_objects.select_for_update(of=("self",))
        .select_related("case__onboarding__venture__legal_employer")
        .get(pk=task.pk)
    )
    actor = _authorize(user, "tvg_offboarding.manage_access_revocation", case=task.case)
    if task.status != AccessRevocationTask.Status.MANUAL_REQUIRED:
        raise ValidationError("This access task is not awaiting manual completion.")
    proof_reference = proof_reference.strip()
    if not proof_reference and not proof_upload:
        raise ValidationError(
            "A non-secret proof reference or proof document is required."
        )
    if proof_upload:
        inspected = inspect_candidate_document(
            proof_upload, allowed_extensions=(".pdf",), max_bytes=10 * 1024 * 1024
        )
        task.proof_file.save(
            f"{task.system_key}-revocation-proof.pdf",
            ContentFile(inspected.content),
            save=False,
        )
        task.proof_sha256 = inspected.sha256
    task.proof_reference = proof_reference
    task.status = AccessRevocationTask.Status.REVOKED
    task.revoked_by = actor
    task.revoked_at = timezone.now()
    task.last_error = ""
    task.save()
    manual = getattr(task, "manual_task", None)
    if manual:
        manual.status = ManualAccessRevocationTask.Status.COMPLETED
        manual.completed_by = actor
        manual.completed_at = timezone.now()
        manual.save(update_fields=("status", "completed_by", "completed_at"))
    if task.proof_file:
        _ensure_retention_schedule(
            case=task.case,
            record_kind=RetentionPolicy.RecordKind.ACCESS_PROOF,
            object_public_id=task.public_id,
            required=True,
        )
    _event(task.case, "completion.manual_access_revoked", actor, obj=task)
    refresh_completion_state(case=task.case, actor=actor)
    return task


@transaction.atomic
def approve_access_exception(*, user, task, reason):
    task = (
        AccessRevocationTask.all_objects.select_for_update(of=("self",))
        .select_related("case__onboarding__venture__legal_employer")
        .get(pk=task.pk)
    )
    actor = _authorize(
        user, "tvg_offboarding.approve_completion_exception", case=task.case
    )
    if not reason.strip():
        raise ValidationError("An exception reason is required.")
    if task.status == AccessRevocationTask.Status.REVOKED:
        raise ValidationError("Revoked access does not need an exception.")
    exception = AccessRevocationException.all_objects.create(
        revocation=task, reason=reason.strip(), approved_by=actor
    )
    task.status = AccessRevocationTask.Status.EXCEPTED
    task.save(update_fields=("status",))
    _event(task.case, "completion.access_exception_approved", actor, obj=exception)
    refresh_completion_state(case=task.case, actor=actor)
    return exception


@transaction.atomic
def approve_completion_template(*, user, template):
    actor = employee_for_user(user)
    template = CompletionDocumentTemplate.all_objects.select_for_update(of=("self",)).get(
        pk=template.pk
    )
    if actor.get_company() != template.company:
        raise PermissionDenied("Template belongs to another company.")
    if not (
        user.is_superuser
        or user.has_perm("tvg_offboarding.approve_completion_template")
    ):
        raise PermissionDenied("Template approval permission is required.")
    if template.approved_at:
        raise ValidationError("This template is already approved.")
    template.approved_by = actor
    template.approved_at = timezone.now()
    template.save()
    return template


@transaction.atomic
def approve_retention_policy(*, user, policy):
    actor = employee_for_user(user)
    policy = RetentionPolicy.all_objects.select_for_update(of=("self",)).get(pk=policy.pk)
    if actor.get_company() != policy.company:
        raise PermissionDenied("Retention policy belongs to another company.")
    if not (
        user.is_superuser or user.has_perm("tvg_offboarding.approve_retention_policy")
    ):
        raise PermissionDenied("Retention-policy approval permission is required.")
    if policy.approved_at:
        raise ValidationError("This retention policy is already approved.")
    policy.approved_by = actor
    policy.approved_at = timezone.now()
    policy.save()
    return policy


def _ensure_retention_schedule(*, case, record_kind, object_public_id, required):
    existing = RetentionSchedule.all_objects.filter(
        case=case,
        record_kind=record_kind,
        object_public_id=str(object_public_id),
    ).first()
    if existing:
        return existing
    policy = (
        RetentionPolicy.all_objects.filter(
            company=case.onboarding.venture.legal_employer,
            record_kind=record_kind,
            approved_at__isnull=False,
            approved_by__isnull=False,
            is_active=True,
        )
        .order_by("-approved_at", "-version_number", "-pk")
        .first()
    )
    if not policy:
        if required:
            raise ValidationError(
                f"Configure an approved retention policy for {record_kind}."
            )
        return None
    retain_until = (
        timezone.localdate() + timedelta(days=policy.retention_days)
        if policy.action == RetentionPolicy.Action.REVIEW
        else None
    )
    return RetentionSchedule.all_objects.create(
        case=case,
        policy=policy,
        record_kind=record_kind,
        object_public_id=str(object_public_id),
        retain_until=retain_until,
    )


def _asset_summary(case):
    rows = []
    assignments = AssetAssignment._base_manager.filter(
        assigned_to_employee_id=case.employee
    ).select_related("asset_id", "asset_item_id")
    for assignment in assignments:
        serial = (
            assignment.asset_item_id.tracking_id
            if assignment.asset_item_id_id
            else assignment.asset_id.asset_tracking_id
        )
        returned = (
            assignment.return_date.isoformat()
            if assignment.return_date
            else "outstanding"
        )
        rows.append(
            f"{assignment.asset_id.asset_name} ({serial}) — {assignment.return_status or 'not returned'} — {returned}"
        )
    return "\n".join(rows) or "No company assets assigned."


@transaction.atomic
def generate_completion_document(*, user, case, template, issue_date=None):
    actor = _authorize(user, "tvg_offboarding.manage_intern_completion", case=case)
    case = (
        CompletionCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "onboarding__erasmus_case",
            "onboarding__department",
            "onboarding__supervisor",
            "onboarding__venture__legal_employer",
            "employee",
        )
        .get(pk=case.pk)
    )
    template = CompletionDocumentTemplate.all_objects.get(pk=template.pk)
    company = case.onboarding.venture.legal_employer
    if template.company_id != company.pk or not template.approved_at:
        raise ValidationError("Use a human-approved template from this company.")
    if (
        template.kind == CompletionDocumentTemplate.Kind.ASSET_RETURN
        and _outstanding_assets(case).exists()
    ):
        raise ValidationError(
            "Return all company assets before preparing the handover document."
        )
    if template.kind == CompletionDocumentTemplate.Kind.REFERENCE_LETTER:
        request = getattr(case, "reference_request", None)
        if not request or request.status != ReferenceLetterRequest.Status.APPROVED:
            raise ValidationError("Approve the optional reference request first.")
    issue_date = issue_date or timezone.localdate()
    context = {
        "company_name": company.company,
        "intern_id": case.onboarding.intern_id,
        "employee_name": case.employee.get_full_name(),
        "internship_start_date": case.onboarding.start_date.isoformat(),
        "internship_end_date": case.onboarding.end_date.isoformat(),
        "university": case.onboarding.erasmus_case.university,
        "programme": case.onboarding.erasmus_case.programme,
        "department": str(case.onboarding.department),
        "supervisor_name": case.onboarding.supervisor.get_full_name(),
        "issue_date": issue_date.isoformat(),
        "asset_summary": _asset_summary(case),
    }
    content, rendered, digest = render_completion_document(
        template=template, context=context
    )
    last_version = (
        CompletionDocument.all_objects.filter(case=case, kind=template.kind)
        .order_by("-version_number")
        .values_list("version_number", flat=True)
        .first()
        or 0
    )
    document = CompletionDocument(
        case=case,
        kind=template.kind,
        template=template,
        version_number=last_version + 1,
        issue_date=issue_date,
        rendered_text=rendered,
        sha256=digest,
        required_signer_roles=list(template.required_signer_roles),
        prepared_by=actor,
    )
    document.pdf_file.save(
        f"{template.kind}-v{document.version_number}.pdf",
        ContentFile(content),
        save=False,
    )
    document.save()
    record_kind = {
        CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE: RetentionPolicy.RecordKind.COMPLETION_CERTIFICATE,
        CompletionDocumentTemplate.Kind.ASSET_RETURN: RetentionPolicy.RecordKind.ASSET_RETURN,
        CompletionDocumentTemplate.Kind.REFERENCE_LETTER: RetentionPolicy.RecordKind.REFERENCE_LETTER,
    }[template.kind]
    _ensure_retention_schedule(
        case=case,
        record_kind=record_kind,
        object_public_id=document.public_id,
        required=True,
    )
    if template.kind == CompletionDocumentTemplate.Kind.REFERENCE_LETTER:
        request = case.reference_request
        request.document = document
        request.status = ReferenceLetterRequest.Status.PREPARED
        request.handled_by = actor
        request.handled_at = timezone.now()
        request.save(update_fields=("document", "status", "handled_by", "handled_at"))
    _event(case, "completion.document_prepared", actor, obj=document)
    refresh_completion_state(case=case, actor=actor)
    return document


@transaction.atomic
def record_document_signature(
    *, user, document, signer_role, signed_at=None, evidence_reference=""
):
    document = (
        CompletionDocument.all_objects.select_for_update(of=("self",))
        .select_related(
            "case__employee",
            "case__onboarding__supervisor",
            "case__onboarding__venture__legal_employer",
        )
        .get(pk=document.pk)
    )
    actor = employee_for_user(user)
    if actor.get_company() != document.case.onboarding.venture.legal_employer:
        raise PermissionDenied("Document belongs to another company.")
    if signer_role not in document.required_signer_roles:
        raise ValidationError(
            "This signer role is not required by the approved template."
        )
    if signer_role == CompletionDocumentTemplate.SignerRole.INTERN:
        if actor.pk != document.case.employee_id:
            raise PermissionDenied("Only the intern can attest the intern signature.")
    elif signer_role == CompletionDocumentTemplate.SignerRole.SUPERVISOR:
        if actor.pk != document.case.onboarding.supervisor_id:
            raise PermissionDenied(
                "Only the assigned supervisor can attest this signature."
            )
    else:
        _authorize(
            user,
            "tvg_offboarding.authorize_completion_document",
            case=document.case,
        )
    signature = CompletionDocumentSignature.all_objects.create(
        document=document,
        signer_role=signer_role,
        signed_at=signed_at or timezone.now(),
        recorded_by=actor,
        evidence_reference=evidence_reference.strip(),
    )
    present = set(document.signatures.values_list("signer_role", flat=True))
    if set(document.required_signer_roles).issubset(present):
        document.status = CompletionDocument.Status.SIGNED
        document.save(update_fields=("status",))
        if document.kind == CompletionDocumentTemplate.Kind.ASSET_RETURN:
            _set_requirement(
                document.case,
                CompletionRequirement.Code.ASSET_RETURN_DOCUMENT,
                completed=True,
                actor=actor,
                source_type="CompletionDocument",
                source_reference=document.public_id,
            )
    _event(
        document.case, "completion.document_signature_recorded", actor, obj=signature
    )
    refresh_completion_state(case=document.case, actor=actor)
    return signature


@transaction.atomic
def issue_completion_certificate(*, user, document):
    document = (
        CompletionDocument.all_objects.select_for_update(of=("self",))
        .select_related("case__onboarding__venture__legal_employer")
        .get(pk=document.pk)
    )
    actor = _authorize(
        user, "tvg_offboarding.manage_intern_completion", case=document.case
    )
    if document.kind != CompletionDocumentTemplate.Kind.COMPLETION_CERTIFICATE:
        raise ValidationError("Only completion certificates use this issue action.")
    if document.status != CompletionDocument.Status.SIGNED:
        raise ValidationError("Record every required human signature before issue.")
    latest = (
        CompletionDocument.all_objects.filter(case=document.case, kind=document.kind)
        .order_by("-version_number")
        .first()
    )
    if latest.pk != document.pk:
        raise ValidationError("Only the latest certificate version can be issued.")
    document.status = CompletionDocument.Status.ISSUED
    document.issued_by = actor
    document.issued_at = timezone.now()
    document.save(update_fields=("status", "issued_by", "issued_at"))
    _set_requirement(
        document.case,
        CompletionRequirement.Code.COMPLETION_CERTIFICATE,
        completed=True,
        actor=actor,
        source_type="CompletionDocument",
        source_reference=document.public_id,
    )
    _event(document.case, "completion.certificate_issued", actor, obj=document)
    refresh_completion_state(case=document.case, actor=actor)
    return document


@transaction.atomic
def request_reference_letter(*, user, case):
    employee = employee_for_user(user)
    case = CompletionCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
    if employee.pk != case.employee_id:
        raise PermissionDenied("Only the intern can request a reference letter.")
    request, created = ReferenceLetterRequest.all_objects.get_or_create(
        case=case, defaults={"requested_by": employee}
    )
    if created:
        _event(case, "completion.reference_requested", employee, obj=request)
    return request


@transaction.atomic
def handle_reference_request(*, user, request, status):
    request = (
        ReferenceLetterRequest.all_objects.select_for_update(of=("self",))
        .select_related("case__onboarding__venture__legal_employer")
        .get(pk=request.pk)
    )
    actor = _authorize(
        user, "tvg_offboarding.manage_intern_completion", case=request.case
    )
    if status not in {
        ReferenceLetterRequest.Status.APPROVED,
        ReferenceLetterRequest.Status.DECLINED,
        ReferenceLetterRequest.Status.DELIVERED,
    }:
        raise ValidationError("Choose a valid human-managed reference status.")
    if status == ReferenceLetterRequest.Status.DELIVERED and (
        not request.document_id
        or request.document.status
        not in {
            CompletionDocument.Status.SIGNED,
            CompletionDocument.Status.ISSUED,
        }
    ):
        raise ValidationError("Prepare and sign the reference letter before delivery.")
    request.status = status
    request.handled_by = actor
    request.handled_at = timezone.now()
    request.save(update_fields=("status", "handled_by", "handled_at"))
    _event(request.case, f"completion.reference_{status}", actor, obj=request)
    return request


@transaction.atomic
def approve_requirement_exception(*, user, requirement, reason):
    requirement = (
        CompletionRequirement.all_objects.select_for_update(of=("self",))
        .select_related("case__onboarding__venture__legal_employer")
        .get(pk=requirement.pk)
    )
    actor = _authorize(
        user, "tvg_offboarding.approve_completion_exception", case=requirement.case
    )
    if requirement.code in {
        CompletionRequirement.Code.ADMINISTRATIVE_CLOSURE,
        CompletionRequirement.Code.ASSET_RETURN,
        CompletionRequirement.Code.ACCESS_REVOCATION,
    }:
        raise ValidationError(
            "Use the source-specific asset or access process for this requirement."
        )
    if requirement.status == CompletionRequirement.Status.COMPLETED:
        raise ValidationError("Completed requirements do not need an exception.")
    if not reason.strip():
        raise ValidationError("An exception reason is required.")
    exception = CompletionException.all_objects.create(
        requirement=requirement,
        reason=reason.strip(),
        approved_by=actor,
    )
    requirement.status = CompletionRequirement.Status.EXCEPTED
    requirement.completed_by = actor
    requirement.completed_at = exception.approved_at
    requirement.save(update_fields=("status", "completed_by", "completed_at"))
    _event(requirement.case, "completion.requirement_excepted", actor, obj=exception)
    refresh_completion_state(case=requirement.case, actor=actor)
    return exception


@transaction.atomic
def confirm_administrative_closure(*, user, case):
    actor = _authorize(user, "tvg_offboarding.complete_intern_completion", case=case)
    case = CompletionCase.all_objects.select_for_update(of=("self",)).get(pk=case.pk)
    other_pending = (
        case.requirements.filter(required=True)
        .exclude(code=CompletionRequirement.Code.ADMINISTRATIVE_CLOSURE)
        .filter(status=CompletionRequirement.Status.PENDING)
    )
    if other_pending.exists():
        raise ValidationError("Resolve every other mandatory requirement first.")
    _set_requirement(
        case,
        CompletionRequirement.Code.ADMINISTRATIVE_CLOSURE,
        completed=True,
        actor=actor,
        source_type="HumanConfirmation",
        source_reference=actor.pk,
    )
    _event(case, "completion.administrative_closure_confirmed", actor)
    return refresh_completion_state(case=case, actor=actor)


@transaction.atomic
def complete_internship(*, user, case):
    actor = _authorize(user, "tvg_offboarding.complete_intern_completion", case=case)
    case = (
        CompletionCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "employee__employee_user_id", "native_offboarding_employee__stage_id"
        )
        .get(pk=case.pk)
    )
    if timezone.localdate() < case.internship_end_date:
        raise ValidationError("The internship end date has not been reached.")
    refresh_completion_state(case=case, actor=actor)
    case.refresh_from_db()
    pending = case.requirements.filter(
        required=True, status=CompletionRequirement.Status.PENDING
    )
    if pending.exists():
        raise ValidationError(
            "Mandatory completion requirements remain: "
            + ", ".join(pending.values_list("title", flat=True))
        )
    if _outstanding_assets(case).exists():
        raise ValidationError("Outstanding native asset assignments block completion.")
    if case.access_revocations.exclude(
        status__in=(
            AccessRevocationTask.Status.REVOKED,
            AccessRevocationTask.Status.EXCEPTED,
        )
    ).exists():
        raise ValidationError("Outstanding access revocations block completion.")
    archived = (
        case.native_offboarding_employee.stage_id.offboarding_id.offboardingstage_set.filter(
            type="archived"
        )
        .order_by("sequence", "pk")
        .first()
    )
    if not archived:
        raise ValidationError("The native offboarding process needs an Archived stage.")
    native = case.native_offboarding_employee
    native.stage_id = archived
    native.save(update_fields=("stage_id",))
    case.employee.is_active = False
    case.employee.save(update_fields=("is_active",))
    if case.employee.employee_user_id:
        account = case.employee.employee_user_id
        account.is_active = False
        account.save(update_fields=("is_active",))
    case.state = CompletionCase.State.COMPLETED
    case.completed_by = actor
    case.completed_at = timezone.now()
    case.save(update_fields=("state", "completed_by", "completed_at"))
    for recipient in {
        case.onboarding.supervisor_id: case.onboarding.supervisor
    }.values():
        _schedule_notification(
            case=case,
            recipient=recipient,
            kind=CompletionNotification.Kind.COMPLETED,
            suffix="final",
        )
    _event(case, "completion.completed", actor)
    return case


def mark_retention_reviews_due(*, as_of=None):
    as_of = as_of or timezone.localdate()
    return RetentionSchedule.all_objects.filter(
        status=RetentionSchedule.Status.RETAINED,
        retain_until__isnull=False,
        retain_until__lte=as_of,
    ).update(status=RetentionSchedule.Status.REVIEW_DUE)


def reconcile_open_completions():
    reconciled = []
    for case in CompletionCase.all_objects.exclude(
        state=CompletionCase.State.COMPLETED
    ).iterator():
        reconciled.append(refresh_completion_state(case=case))
    return reconciled


def _notification_verb(item):
    return {
        CompletionNotification.Kind.UPCOMING_END: "Internship end date is approaching",
        CompletionNotification.Kind.FINAL_REPORTING: "Final internship reporting is requested",
        CompletionNotification.Kind.FINAL_PRESENTATION: "Final internship presentation is scheduled",
        CompletionNotification.Kind.REVIEW_REQUIRED: "Final internship review is required",
        CompletionNotification.Kind.ASSETS_OUTSTANDING: "Internship assets remain outstanding",
        CompletionNotification.Kind.ACCESS_REVOCATION: "Internship access revocation is required",
        CompletionNotification.Kind.COMPLETED: "Internship completion is finalized",
    }[item.kind]


@transaction.atomic
def dispatch_due_notifications(*, as_of=None):
    as_of = as_of or timezone.now()
    queued = list(
        CompletionNotification.all_objects.select_for_update(of=("self",))
        .select_related("recipient__employee_user_id", "case__employee")
        .filter(status=CompletionNotification.Status.PENDING, scheduled_for__lte=as_of)
    )
    delivered = []
    for item in queued:
        recipient = item.recipient.employee_user_id
        if not recipient:
            continue
        responses = notify.send(
            item.case.employee,
            recipient=recipient,
            verb=_notification_verb(item),
            target=item.case,
            public=False,
            redirect=f"/completion/{item.case.public_id}/",
        )
        created = next((rows[0] for _receiver, rows in responses if rows), None)
        item.native_notification_id = getattr(created, "pk", None)
        item.status = CompletionNotification.Status.DELIVERED
        item.delivered_at = as_of
        item.save(update_fields=("native_notification_id", "status", "delivered_at"))
        delivered.append(item)
    return delivered


def management_cases(*, user):
    viewer = employee_for_user(user)
    if not (
        user.is_superuser
        or user.has_perm("tvg_offboarding.view_completion_management")
        or user.has_perm("tvg_offboarding.manage_intern_completion")
    ):
        raise PermissionDenied("Completion management permission is required.")
    return CompletionCase.all_objects.filter(
        onboarding__venture__legal_employer_id=company_id_for(viewer)
    ).select_related("employee", "onboarding__supervisor")
