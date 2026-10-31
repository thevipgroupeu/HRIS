from __future__ import annotations

from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.module_loading import import_string

from asset.models import Asset, AssetAssignment, AssetItem
from base.models import CompanyGroupAssignment
from employee.models import Employee, EmployeeWorkInformation
from onboarding.models import (
    CandidateStage,
    CandidateTask,
    OnboardingStage,
    OnboardingTask,
)
from tvg_erasmus.models import ErasmusAccommodationRequest, ErasmusCase
from tvg_onboarding.documents import render_asset_acknowledgment
from tvg_onboarding.integrations import DisabledProvisioningProvider
from tvg_onboarding.models import (
    AccountProvisioningRequest,
    AssetAcknowledgmentTemplate,
    InternOnboarding,
    ManualITTask,
    OnboardingAccessGrant,
    OnboardingAccessProfile,
    OnboardingAssetAllocation,
    OnboardingChecklistLink,
    OnboardingEvent,
    OnboardingPolicy,
    ScheduledOnboardingCommunication,
    StaffIdSequence,
)

CHECKLIST = (
    ("legal_documents", "Legal documents confirmed"),
    ("supervisor", "Supervisor assigned"),
    ("dates", "Start and end dates confirmed"),
    ("welcome_email", "Welcome email scheduled"),
    ("arrival_instructions", "Arrival reminder and office instructions"),
    ("accommodation", "Accommodation support request reviewed"),
    ("welcome_book", "Welcome Book assigned"),
    ("orientation", "Orientation scheduled"),
    ("intern_id", "Intern ID generated"),
    ("account", "Company account provisioning requested"),
    ("access", "Department and venture permissions assigned"),
    ("equipment", "Laptop or equipment allocated"),
    ("asset_ack", "Asset allocation form prepared and acknowledged"),
    ("training", "Training programme assigned"),
    ("first_week", "First-week tasks issued"),
)


def _employee_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("A signed-in staff member is required.")
    try:
        return user.employee_get
    except Employee.DoesNotExist as exc:
        raise PermissionDenied(
            "The user must be linked to an employee record."
        ) from exc


def _company(onboarding):
    return onboarding.venture.legal_employer


def _authorize(user, permission, *, onboarding=None):
    actor = _employee_for_user(user)
    if not (user.is_superuser or user.has_perm(permission)):
        raise PermissionDenied("You do not have permission for this onboarding action.")
    if onboarding and actor.get_company() != _company(onboarding):
        raise PermissionDenied("The onboarding belongs to another legal employer.")
    return actor


def _event(onboarding, event_type, actor=None, **payload):
    return OnboardingEvent.all_objects.create(
        onboarding=onboarding,
        event_type=event_type,
        actor=actor,
        payload=payload,
    )


def _eligible_case(case):
    errors = []
    if case.acceptance_status != ErasmusCase.AcceptanceStatus.RECEIVED:
        errors.append("Acceptance receipt must be confirmed.")
    if not case.confirmed_start_date or not case.confirmed_end_date:
        errors.append("Confirmed start and end dates are required.")
    if not case.before_mobility_completed_at:
        errors.append("Mandatory Before Mobility documents must be complete.")
    if case.arrangement_status != ErasmusCase.ArrangementStatus.HR_APPROVED:
        errors.append("HR-approved arrangements are required.")
    if errors:
        raise ValidationError(errors)


def _policy(company):
    policy, _created = OnboardingPolicy.all_objects.get_or_create(company=company)
    return policy


def _next_identifier(*, policy, year, kind):
    sequence, _created = StaffIdSequence.all_objects.select_for_update(of=("self",)).get_or_create(
        company=policy.company,
        year=year,
        kind=kind,
        defaults={"next_number": 1},
    )
    prefix = (
        policy.intern_id_prefix
        if kind == StaffIdSequence.Kind.INTERN
        else policy.employee_id_prefix
    )
    while True:
        number = sequence.next_number
        sequence.next_number += 1
        sequence.save(update_fields=("next_number",))
        identifier = f"{prefix}-{year}-{number:04d}"
        if kind == StaffIdSequence.Kind.INTERN:
            exists = InternOnboarding.all_objects.filter(intern_id=identifier).exists()
        else:
            exists = (
                InternOnboarding.all_objects.filter(
                    employee_badge_id=identifier
                ).exists()
                or Employee._base_manager.filter(badge_id=identifier).exists()
            )
        if not exists:
            return identifier


def _native_stage(case):
    recruitment = case.application.opening.recruitment
    stage, _created = OnboardingStage._base_manager.get_or_create(
        recruitment_id=recruitment,
        stage_title="THEVIPGROUP Intern Onboarding",
        defaults={
            "sequence": (
                OnboardingStage._base_manager.filter(
                    recruitment_id=recruitment
                ).aggregate(value=Max("sequence"))["value"]
                or 0
            )
            + 1,
            "is_final_stage": False,
        },
    )
    return stage


def _aware_at(day, hour=9):
    value = datetime.combine(day, time(hour=hour))
    return timezone.make_aware(value, timezone.get_current_timezone())


def _complete_link(link, actor, note):
    link.native_task.status = "done"
    link.native_task.save(update_fields=("status",))
    link.completed_by = actor
    link.completed_at = timezone.now()
    link.evidence_note = note
    link.save(update_fields=("completed_by", "completed_at", "evidence_note"))


def _initialize_native_checklist(onboarding, actor, policy):
    candidate = onboarding.erasmus_case.candidate
    stage = onboarding.native_stage
    accommodation = ErasmusAccommodationRequest.all_objects.filter(
        case=onboarding.erasmus_case
    ).first()
    automatic = {
        "legal_documents": "Before Mobility documents completed.",
        "supervisor": "Supervisor copied from HR-approved arrangements.",
        "dates": "Dates copied from HR-approved arrangements.",
        "welcome_email": "Welcome communication scheduled.",
        "arrival_instructions": "Arrival communication scheduled.",
        "intern_id": f"Generated {onboarding.intern_id}.",
        "account": "Provisioning request created.",
    }
    if (
        not accommodation
        or not accommodation.needs_assistance
        or accommodation.status != ErasmusAccommodationRequest.Status.PENDING
    ):
        automatic["accommodation"] = (
            "No accommodation assistance follow-up is required."
        )
    for sequence, (code, title) in enumerate(CHECKLIST, start=1):
        task, _created = OnboardingTask._base_manager.get_or_create(
            stage_id=stage,
            task_title=title,
            defaults={"is_required": True},
        )
        if not task.is_required:
            task.is_required = True
            task.save(update_fields=("is_required",))
        task.candidates.add(candidate)
        task.employee_id.add(actor, onboarding.supervisor)
        candidate_task, _created = CandidateTask._base_manager.get_or_create(
            candidate_id=candidate,
            stage_id=stage,
            onboarding_task_id=task,
            defaults={"status": "todo"},
        )
        due_date = None
        if code == "welcome_email":
            due_date = onboarding.start_date - timedelta(
                days=policy.welcome_email_lead_days
            )
        elif code == "arrival_instructions":
            due_date = onboarding.start_date - timedelta(days=1)
        link, _created = OnboardingChecklistLink.all_objects.get_or_create(
            onboarding=onboarding,
            code=code,
            defaults={
                "native_task": candidate_task,
                "due_date": due_date,
                "required": True,
            },
        )
        if code in automatic and candidate_task.status != "done":
            _complete_link(link, actor, automatic[code])


@transaction.atomic
def start_intern_onboarding(*, user, case):
    case = (
        ErasmusCase.all_objects.select_for_update(of=("self",))
        .select_related(
            "application__person",
            "application__opening__venture__legal_employer",
            "application__opening__recruitment",
            "candidate",
            "department",
            "supervisor",
        )
        .get(pk=case.pk)
    )
    actor = _employee_for_user(user)
    if not (
        user.is_superuser or user.has_perm("tvg_onboarding.start_intern_onboarding")
    ):
        raise PermissionDenied("You cannot start intern onboarding.")
    company = case.application.opening.venture.legal_employer
    if actor.get_company() != company:
        raise PermissionDenied("The case belongs to another legal employer.")
    existing = InternOnboarding.all_objects.filter(erasmus_case=case).first()
    if existing:
        return existing
    _eligible_case(case)
    if case.application.person.employee_id or case.candidate.converted_employee_id_id:
        raise ValidationError(
            "This person or candidate is already linked to an employee."
        )
    policy = _policy(company)
    year = case.confirmed_start_date.year
    stage = _native_stage(case)
    candidate_stage, _created = CandidateStage._base_manager.update_or_create(
        candidate_id=case.candidate,
        defaults={"onboarding_stage_id": stage, "sequence": stage.sequence or 0},
    )
    onboarding = InternOnboarding.all_objects.create(
        erasmus_case=case,
        person=case.application.person,
        native_stage=stage,
        native_candidate_stage=candidate_stage,
        intern_id=_next_identifier(
            policy=policy, year=year, kind=StaffIdSequence.Kind.INTERN
        ),
        employee_badge_id=_next_identifier(
            policy=policy, year=year, kind=StaffIdSequence.Kind.EMPLOYEE
        ),
        start_date=case.confirmed_start_date,
        end_date=case.confirmed_end_date,
        department=case.department,
        venture=case.application.opening.venture,
        supervisor=case.supervisor,
    )
    case.candidate.start_onboard = True
    case.candidate.save(update_fields=("start_onboard",))
    ScheduledOnboardingCommunication.all_objects.bulk_create(
        [
            ScheduledOnboardingCommunication(
                onboarding=onboarding,
                kind=ScheduledOnboardingCommunication.Kind.WELCOME,
                scheduled_for=_aware_at(
                    onboarding.start_date
                    - timedelta(days=policy.welcome_email_lead_days)
                ),
            ),
            ScheduledOnboardingCommunication(
                onboarding=onboarding,
                kind=ScheduledOnboardingCommunication.Kind.ARRIVAL,
                scheduled_for=_aware_at(onboarding.start_date - timedelta(days=1)),
            ),
        ]
    )
    provisioning_status = (
        AccountProvisioningRequest.Status.MANUAL_REQUIRED
        if policy.identity_provider == OnboardingPolicy.Provider.MANUAL
        else AccountProvisioningRequest.Status.REQUESTED
    )
    request = AccountProvisioningRequest.all_objects.create(
        onboarding=onboarding,
        provider=policy.identity_provider,
        status=provisioning_status,
        requested_by=actor,
    )
    if provisioning_status == AccountProvisioningRequest.Status.MANUAL_REQUIRED:
        ManualITTask.all_objects.create(
            provisioning_request=request,
            title="Provision company identity and email",
            instructions=(
                "Use an authorized administrative channel, then record only the assigned "
                "work email and non-secret external reference. Never store credentials here."
            ),
        )
    _initialize_native_checklist(onboarding, actor, policy)
    _event(onboarding, "onboarding.started", actor, status=onboarding.status)
    return onboarding


@transaction.atomic
def approve_intern_onboarding(*, user, onboarding):
    onboarding = InternOnboarding.all_objects.select_for_update(of=("self",)).get(pk=onboarding.pk)
    actor = _authorize(
        user, "tvg_onboarding.approve_intern_onboarding", onboarding=onboarding
    )
    _eligible_case(onboarding.erasmus_case)
    if onboarding.status == InternOnboarding.Status.ONBOARDED:
        raise ValidationError("Completed onboarding cannot be approved again.")
    onboarding.status = InternOnboarding.Status.APPROVED
    onboarding.approved_by = actor
    onboarding.approved_at = timezone.now()
    onboarding.save(update_fields=("status", "approved_by", "approved_at"))
    _event(onboarding, "onboarding.approved", actor)
    return onboarding


def _provisioning_backend(provider):
    path = getattr(settings, "TVG_ONBOARDING_PROVISIONING_BACKENDS", {}).get(provider)
    if not path:
        return DisabledProvisioningProvider()
    return import_string(path)()


@transaction.atomic
def dispatch_account_provisioning(*, user, onboarding, requested_email=""):
    onboarding = InternOnboarding.all_objects.select_for_update(of=("self",)).get(pk=onboarding.pk)
    actor = _authorize(
        user, "tvg_onboarding.manage_account_provisioning", onboarding=onboarding
    )
    request = AccountProvisioningRequest.all_objects.select_for_update(of=("self",)).get(
        onboarding=onboarding
    )
    if onboarding.status not in (
        InternOnboarding.Status.APPROVED,
        InternOnboarding.Status.IN_PROGRESS,
    ):
        raise ValidationError("HR approval is required before provisioning.")
    if request.status == AccountProvisioningRequest.Status.PROVISIONED:
        return request
    request.requested_email = requested_email.strip().casefold()
    request.save(update_fields=("requested_email",))
    result = _provisioning_backend(request.provider).provision(
        request=request, onboarding=onboarding
    )
    if not result.supported:
        request.status = AccountProvisioningRequest.Status.MANUAL_REQUIRED
        request.last_error = result.message[:255]
        request.save(update_fields=("status", "last_error"))
        ManualITTask.all_objects.get_or_create(
            provisioning_request=request,
            defaults={
                "title": "Provision company identity and email",
                "instructions": (
                    "Use an authorized administrative channel, then record the assigned "
                    "work email. Do not store credentials."
                ),
            },
        )
        _event(onboarding, "account.manual_required", actor, provider=request.provider)
        return request
    if not result.completed or not result.email:
        request.status = AccountProvisioningRequest.Status.FAILED
        request.last_error = (result.message or "Provider did not confirm completion.")[
            :255
        ]
        request.save(update_fields=("status", "last_error"))
        _event(
            onboarding, "account.provisioning_failed", actor, provider=request.provider
        )
        return request
    request.status = AccountProvisioningRequest.Status.PROVISIONED
    request.provisioned_email = result.email.strip().casefold()
    request.external_reference = result.external_reference
    request.completed_by = actor
    request.completed_at = timezone.now()
    request.last_error = ""
    request.save()
    _event(onboarding, "account.provisioned", actor, provider=request.provider)
    return request


@transaction.atomic
def complete_manual_provisioning(
    *, user, onboarding, work_email, external_reference=""
):
    onboarding = InternOnboarding.all_objects.select_for_update(of=("self",)).get(pk=onboarding.pk)
    actor = _authorize(
        user, "tvg_onboarding.manage_account_provisioning", onboarding=onboarding
    )
    if onboarding.status not in (
        InternOnboarding.Status.APPROVED,
        InternOnboarding.Status.IN_PROGRESS,
    ):
        raise ValidationError("HR approval is required before provisioning.")
    normalized = work_email.strip().casefold()
    if not normalized:
        raise ValidationError("A confirmed company work email is required.")
    request = AccountProvisioningRequest.all_objects.select_for_update(of=("self",)).get(
        onboarding=onboarding
    )
    if request.status == AccountProvisioningRequest.Status.PROVISIONED:
        if request.provisioned_email == normalized:
            return request
        raise ValidationError("Provisioned work email cannot be silently changed.")
    if Employee._base_manager.filter(email__iexact=normalized).exists():
        raise ValidationError("That work email is already linked to an employee.")
    request.status = AccountProvisioningRequest.Status.PROVISIONED
    request.provisioned_email = normalized
    request.external_reference = external_reference.strip()
    request.completed_by = actor
    request.completed_at = timezone.now()
    request.last_error = ""
    request.save()
    task, _created = ManualITTask.all_objects.get_or_create(
        provisioning_request=request,
        defaults={
            "title": "Provision company identity and email",
            "instructions": "Completed through an authorized manual administrative channel.",
        },
    )
    task.status = ManualITTask.Status.COMPLETED
    task.completed_by = actor
    task.completed_at = timezone.now()
    task.save(update_fields=("status", "completed_by", "completed_at"))
    _event(onboarding, "account.provisioned_manual", actor)
    return request


@transaction.atomic
def create_employee_for_onboarding(*, user, onboarding, phone=""):
    onboarding = (
        InternOnboarding.all_objects.select_for_update(of=("self",))
        .select_related(
            "erasmus_case__candidate", "person", "supervisor", "department", "venture"
        )
        .get(pk=onboarding.pk)
    )
    actor = _authorize(
        user, "tvg_onboarding.manage_intern_onboarding", onboarding=onboarding
    )
    if onboarding.employee_id:
        return onboarding.employee
    if onboarding.status not in (
        InternOnboarding.Status.APPROVED,
        InternOnboarding.Status.IN_PROGRESS,
    ):
        raise ValidationError("HR approval is required before employee conversion.")
    candidate = onboarding.erasmus_case.candidate
    if onboarding.person.employee_id or candidate.converted_employee_id_id:
        raise ValidationError(
            "This person or candidate already has an employee record."
        )
    request = AccountProvisioningRequest.all_objects.get(onboarding=onboarding)
    if request.status != AccountProvisioningRequest.Status.PROVISIONED:
        raise ValidationError("Company account provisioning must be confirmed first.")
    effective_phone = (phone or candidate.mobile or "").strip()
    if not effective_phone:
        raise ValidationError(
            "A confirmed phone number is required for the employee record."
        )
    names = (
        (candidate.name or onboarding.person.preferred_name or "")
        .strip()
        .split(maxsplit=1)
    )
    if not names:
        raise ValidationError("The candidate name must be confirmed before conversion.")
    employee = Employee(
        badge_id=onboarding.employee_badge_id,
        employee_first_name=names[0],
        employee_last_name=names[1] if len(names) > 1 else "",
        email=request.provisioned_email,
        phone=effective_phone,
        is_from_onboarding=True,
    )
    employee.save()
    EmployeeWorkInformation._base_manager.filter(employee_id=employee).update(
        company_id=onboarding.venture.legal_employer,
        department_id=onboarding.department,
        job_position_id=onboarding.erasmus_case.application.opening.job_position,
        reporting_manager_id=onboarding.supervisor,
        location=onboarding.erasmus_case.work_location,
        email=request.provisioned_email,
        date_joining=onboarding.start_date,
        contract_end_date=onboarding.end_date,
    )
    # Employee.save() creates and caches an initially empty work-info row. Reload
    # after the scoped update so Person.clean() sees the legal employer.
    employee = Employee._base_manager.select_related("employee_work_info").get(
        pk=employee.pk
    )
    onboarding.person.employee = employee
    onboarding.person.save(update_fields=("employee",))
    candidate.converted_employee_id = employee
    candidate.converted = True
    candidate.joining_date = onboarding.start_date
    candidate.save(update_fields=("converted_employee_id", "converted", "joining_date"))
    onboarding.employee = employee
    onboarding.status = InternOnboarding.Status.IN_PROGRESS
    onboarding.save(update_fields=("employee", "status"))
    _event(onboarding, "employee.created", actor, badge_id=employee.badge_id)
    return employee


@transaction.atomic
def approve_access_profile(*, user, profile):
    actor = _employee_for_user(user)
    if not (
        user.is_superuser
        or user.has_perm("tvg_onboarding.approve_onboarding_access_profile")
    ):
        raise PermissionDenied("You cannot approve onboarding access profiles.")
    if actor.get_company() != profile.company:
        raise PermissionDenied("The profile belongs to another company.")
    if not profile.groups.exists():
        raise ValidationError("An access profile must contain at least one group.")
    profile.approved_by = actor
    profile.approved_at = timezone.now()
    profile.approved_group_ids = list(
        profile.groups.order_by("pk").values_list("pk", flat=True)
    )
    profile.save(update_fields=("approved_by", "approved_at", "approved_group_ids"))
    return profile


@transaction.atomic
def grant_onboarding_access(*, user, onboarding, profile):
    onboarding = (
        InternOnboarding.all_objects.select_for_update(of=("self",))
        .select_related("employee__employee_user_id", "venture__legal_employer")
        .get(pk=onboarding.pk)
    )
    actor = _authorize(
        user, "tvg_onboarding.manage_onboarding_access", onboarding=onboarding
    )
    if not onboarding.employee_id:
        raise ValidationError("Create the employee record before assigning access.")
    if not profile.is_active or not profile.approved_at:
        raise ValidationError("An active, human-approved access profile is required.")
    if (
        profile.company_id != onboarding.venture.legal_employer_id
        or profile.venture_id != onboarding.venture_id
        or profile.department_id != onboarding.department_id
    ):
        raise ValidationError("Access profile scope does not match this onboarding.")
    groups = list(
        Group.objects.filter(pk__in=profile.approved_group_ids).order_by("pk")
    )
    if len(groups) != len(profile.approved_group_ids):
        raise ValidationError("The approved access profile references a deleted group.")
    user_account = onboarding.employee.employee_user_id
    if not user_account:
        raise ValidationError("The employee must have an active PeopleOps account.")
    grant, created = OnboardingAccessGrant.all_objects.get_or_create(
        onboarding=onboarding,
        defaults={
            "profile": profile,
            "group_snapshot": [
                {"id": group.pk, "name": group.name} for group in groups
            ],
            "granted_by": actor,
        },
    )
    if not created:
        return grant
    company = onboarding.venture.legal_employer
    for group in groups:
        CompanyGroupAssignment.objects.get_or_create(
            user=user_account, company=company, group=group
        )
        CompanyGroupAssignment.sync_user_group_membership(user_account, group)
    complete_checklist_item(
        user=user,
        onboarding=onboarding,
        code="access",
        evidence_note=f"Approved access profile: {profile.name}.",
    )
    _event(onboarding, "access.granted", actor, profile_id=profile.pk)
    return grant


@transaction.atomic
def allocate_asset(*, user, onboarding, asset, asset_item=None):
    onboarding = (
        InternOnboarding.all_objects.select_for_update(of=("self",))
        .select_related("employee", "venture__legal_employer")
        .get(pk=onboarding.pk)
    )
    actor = _authorize(
        user, "tvg_onboarding.allocate_onboarding_asset", onboarding=onboarding
    )
    if not (user.is_superuser or user.has_perm("asset.add_assetassignment")):
        raise PermissionDenied(
            "PeopleOps asset-allocation permission is required."
        )
    if not onboarding.employee_id:
        raise ValidationError("Create the employee record before allocating equipment.")
    asset = Asset._base_manager.select_for_update(of=("self",)).get(pk=asset.pk)
    company = onboarding.venture.legal_employer
    if not asset.asset_category_id.company_id.filter(pk=company.pk).exists():
        raise ValidationError("Asset belongs to another company.")
    if asset_item is None:
        asset_item = asset.asset_items.filter(status="Available").order_by("pk").first()
    else:
        asset_item = AssetItem._base_manager.select_for_update(of=("self",)).get(pk=asset_item.pk)
    if (
        not asset_item
        or asset_item.asset_id_id != asset.pk
        or asset_item.status != "Available"
    ):
        raise ValidationError("An available asset item from this asset is required.")
    assignment = AssetAssignment._base_manager.create(
        asset_id=asset,
        asset_item_id=asset_item,
        assigned_to_employee_id=onboarding.employee,
        assigned_by_employee_id=actor,
    )
    asset_item.status = "In use"
    asset_item.save(update_fields=("status",))
    allocation = OnboardingAssetAllocation.all_objects.create(
        onboarding=onboarding, native_assignment=assignment
    )
    complete_checklist_item(
        user=user,
        onboarding=onboarding,
        code="equipment",
        evidence_note=f"Allocated asset serial {asset_item.tracking_id}.",
    )
    _event(onboarding, "asset.allocated", actor, assignment_id=assignment.pk)
    return allocation


@transaction.atomic
def approve_asset_template(*, user, template):
    actor = _employee_for_user(user)
    if not (
        user.is_superuser or user.has_perm("tvg_onboarding.approve_asset_ack_template")
    ):
        raise PermissionDenied("You cannot approve asset acknowledgment templates.")
    if actor.get_company() != template.company:
        raise PermissionDenied("Template belongs to another company.")
    template.approved_by = actor
    template.approved_at = timezone.now()
    template.save()
    return template


@transaction.atomic
def prepare_asset_acknowledgment(*, user, allocation, template):
    allocation = (
        OnboardingAssetAllocation.all_objects.select_for_update(of=("self",))
        .select_related(
            "onboarding__employee",
            "onboarding__venture__legal_employer",
            "native_assignment__asset_id",
            "native_assignment__asset_item_id",
        )
        .get(pk=allocation.pk)
    )
    onboarding = allocation.onboarding
    actor = _authorize(
        user, "tvg_onboarding.allocate_onboarding_asset", onboarding=onboarding
    )
    if (
        not template.approved_at
        or template.company_id != onboarding.venture.legal_employer_id
    ):
        raise ValidationError("A human-approved template for this company is required.")
    assignment = allocation.native_assignment
    serial = (
        assignment.asset_item_id.tracking_id
        if assignment.asset_item_id_id
        else assignment.asset_id.asset_tracking_id
    )
    context = {
        "company_name": onboarding.venture.legal_employer.company,
        "intern_id": onboarding.intern_id,
        "employee_badge_id": onboarding.employee_badge_id,
        "employee_name": onboarding.employee.get_full_name(),
        "asset_name": assignment.asset_id.asset_name,
        "asset_serial_number": serial,
        "allocation_date": assignment.assigned_date.isoformat(),
    }
    content, rendered, digest = render_asset_acknowledgment(
        template=template, context=context
    )
    allocation.template = template
    allocation.document_version += 1
    allocation.rendered_text = rendered
    allocation.sha256 = digest
    allocation.acknowledgment_status = (
        OnboardingAssetAllocation.AcknowledgmentStatus.PREPARED
    )
    allocation.prepared_by = actor
    allocation.prepared_at = timezone.now()
    allocation.pdf_file.save(
        f"asset-{serial}-acknowledgment-v{allocation.document_version}.pdf",
        ContentFile(content),
        save=False,
    )
    allocation.save()
    _event(
        onboarding,
        "asset.acknowledgment_prepared",
        actor,
        allocation_id=allocation.pk,
        version=allocation.document_version,
    )
    return allocation


@transaction.atomic
def record_asset_acknowledgment(*, user, allocation, reference=""):
    allocation = OnboardingAssetAllocation.all_objects.select_for_update(of=("self",)).get(
        pk=allocation.pk
    )
    onboarding = allocation.onboarding
    actor = _authorize(
        user, "tvg_onboarding.allocate_onboarding_asset", onboarding=onboarding
    )
    if (
        allocation.acknowledgment_status
        != OnboardingAssetAllocation.AcknowledgmentStatus.PREPARED
    ):
        raise ValidationError("Prepare the approved acknowledgment document first.")
    allocation.acknowledgment_status = (
        OnboardingAssetAllocation.AcknowledgmentStatus.ACKNOWLEDGED
    )
    allocation.acknowledged_at = timezone.now()
    allocation.acknowledgment_reference = reference.strip()
    allocation.save(
        update_fields=(
            "acknowledgment_status",
            "acknowledged_at",
            "acknowledgment_reference",
        )
    )
    complete_checklist_item(
        user=user,
        onboarding=onboarding,
        code="asset_ack",
        evidence_note="Asset acknowledgment recorded.",
    )
    _event(onboarding, "asset.acknowledged", actor, allocation_id=allocation.pk)
    return allocation


@transaction.atomic
def complete_checklist_item(*, user, onboarding, code, evidence_note=""):
    onboarding = InternOnboarding.all_objects.select_for_update(of=("self",)).get(pk=onboarding.pk)
    actor = _authorize(
        user, "tvg_onboarding.manage_intern_onboarding", onboarding=onboarding
    )
    link = (
        OnboardingChecklistLink.all_objects.select_for_update(of=("self",))
        .select_related("native_task")
        .get(onboarding=onboarding, code=code)
    )
    if link.native_task.status == "done":
        return link
    _complete_link(link, actor, evidence_note.strip())
    _event(onboarding, "checklist.completed", actor, code=code)
    return link


@transaction.atomic
def complete_intern_onboarding(*, user, onboarding):
    onboarding = (
        InternOnboarding.all_objects.select_for_update(of=("self",))
        .select_related("erasmus_case__candidate", "native_candidate_stage")
        .get(pk=onboarding.pk)
    )
    actor = _authorize(
        user, "tvg_onboarding.complete_intern_onboarding", onboarding=onboarding
    )
    if onboarding.status == InternOnboarding.Status.ONBOARDED:
        return onboarding
    if not onboarding.employee_id:
        raise ValidationError("Employee conversion is incomplete.")
    if not hasattr(onboarding, "access_grant"):
        raise ValidationError("Department and venture access is incomplete.")
    request = AccountProvisioningRequest.all_objects.get(onboarding=onboarding)
    if request.status != AccountProvisioningRequest.Status.PROVISIONED:
        raise ValidationError("Company account provisioning is incomplete.")
    if not onboarding.asset_allocations.filter(
        acknowledgment_status=OnboardingAssetAllocation.AcknowledgmentStatus.ACKNOWLEDGED
    ).exists():
        raise ValidationError("An acknowledged asset allocation is required.")
    pending = list(
        onboarding.checklist_items.filter(required=True)
        .exclude(native_task__status="done")
        .values_list("code", flat=True)
    )
    if pending:
        raise ValidationError(
            {"checklist": f"Required onboarding tasks remain: {', '.join(pending)}"}
        )
    recruitment = onboarding.erasmus_case.application.opening.recruitment
    final_stage, _created = OnboardingStage._base_manager.get_or_create(
        recruitment_id=recruitment,
        stage_title="THEVIPGROUP Onboarded",
        defaults={
            "sequence": (
                OnboardingStage._base_manager.filter(
                    recruitment_id=recruitment
                ).aggregate(value=Max("sequence"))["value"]
                or 0
            )
            + 1,
            "is_final_stage": True,
        },
    )
    candidate_stage = onboarding.native_candidate_stage
    candidate_stage.onboarding_stage_id = final_stage
    candidate_stage.sequence = final_stage.sequence or candidate_stage.sequence
    candidate_stage.save()
    onboarding.status = InternOnboarding.Status.ONBOARDED
    onboarding.onboarded_by = actor
    onboarding.onboarded_at = timezone.now()
    onboarding.save(update_fields=("status", "onboarded_by", "onboarded_at"))
    _event(onboarding, "onboarding.completed", actor)
    return onboarding


def visible_onboardings(user):
    employee = _employee_for_user(user)
    queryset = InternOnboarding.objects.select_related(
        "person", "employee", "venture", "department", "supervisor"
    )
    if user.is_superuser:
        return queryset
    if not (
        user.has_perm("tvg_onboarding.view_internonboarding")
        or user.has_perm("tvg_onboarding.manage_intern_onboarding")
        or user.has_perm("tvg_onboarding.approve_intern_onboarding")
    ):
        return queryset.none()
    return queryset.filter(venture__legal_employer=employee.get_company())
