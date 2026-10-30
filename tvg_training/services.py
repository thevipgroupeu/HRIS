from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from base.models import CompanyGroupAssignment
from employee.models import Employee
from tvg_onboarding.models import InternOnboarding
from tvg_peoplehub.models import PeopleHubLearningAssignment

from .integrations import (
    MoodleEnrollmentRequest,
    MoodleProgressRequest,
    MoodleProgressResult,
    MoodleUserRequest,
    TrainingProviderError,
    get_training_provider,
)
from .models import (
    MoodleAccountLink,
    MoodleEnrollment,
    StaffIdentityLink,
    StaffIdentitySession,
    StaffRoleMapping,
    TrainingAssignment,
    TrainingEvent,
    TrainingNotification,
    TrainingProgramme,
    TrainingProgrammeCourse,
    TrainingSyncAttempt,
)


def _employee_for_user(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("An authenticated employee is required.")
    employee = (
        Employee._base_manager.select_related("employee_work_info__company_id")
        .filter(employee_user_id=user, is_active=True)
        .first()
    )
    if not employee or not employee.get_company():
        raise PermissionDenied("An active company employee is required.")
    return employee


def _authorize(user, permission, *, company):
    actor = _employee_for_user(user)
    if not (user.is_superuser or user.has_perm(permission)):
        raise PermissionDenied("This TrainingHub action is not permitted.")
    if actor.get_company() != company:
        raise PermissionDenied("This TrainingHub object belongs to another company.")
    return actor


def _event(
    *, company, event_type, employee=None, actor=None, object_id=None, **payload
):
    return TrainingEvent.all_objects.create(
        company=company,
        employee=employee,
        actor=actor,
        event_type=event_type,
        object_public_id=object_id,
        payload=payload,
    )


def _employee_venture_id(employee):
    onboarding = getattr(employee, "tvg_intern_onboarding", None)
    if onboarding:
        return onboarding.venture_id
    assignment = getattr(employee, "tvg_venture_assignment", None)
    return assignment.venture_id if assignment else None


def _onboarding_for(employee):
    return getattr(employee, "tvg_intern_onboarding", None)


def _require_training_ready(employee):
    company = employee.get_company()
    user = employee.employee_user_id
    if not employee.is_active or not company or not user or not user.is_active:
        raise ValidationError("Training requires an active employee and user account.")
    onboarding = _onboarding_for(employee)
    if onboarding and onboarding.status != InternOnboarding.Status.ONBOARDED:
        raise ValidationError(
            "Intern training starts only after onboarding is complete."
        )
    return company, onboarding


@transaction.atomic
def approve_training_programme(*, user, programme):
    programme = TrainingProgramme.all_objects.select_for_update(of=("self",)).get(pk=programme.pk)
    actor = _authorize(
        user, "tvg_training.approve_training_programme", company=programme.company
    )
    if programme.is_approved:
        return programme
    if not programme.programme_courses.filter(course__is_active=True).exists():
        raise ValidationError("Add at least one active Moodle course before approval.")
    programme.approved_by = actor
    programme.approved_at = timezone.now()
    programme.is_active = True
    programme.save()
    TrainingProgramme.all_objects.filter(
        company=programme.company, code=programme.code, is_active=True
    ).exclude(pk=programme.pk).update(is_active=False)
    _event(
        company=programme.company,
        event_type="training.programme_approved",
        actor=actor,
        object_id=programme.public_id,
        version=programme.version,
    )
    return programme


def identify_training_programmes(employee):
    company, onboarding = _require_training_ready(employee)
    audience = (
        TrainingProgramme.Audience.INTERN
        if onboarding
        else TrainingProgramme.Audience.EMPLOYEE
    )
    work = employee.employee_work_info
    venture_id = _employee_venture_id(employee)
    return (
        TrainingProgramme.all_objects.filter(
            company=company,
            is_active=True,
            approved_at__isnull=False,
            approved_by__isnull=False,
            audience__in=(TrainingProgramme.Audience.ALL, audience),
        )
        .filter(models.Q(venture__isnull=True) | models.Q(venture_id=venture_id))
        .filter(
            models.Q(department__isnull=True)
            | models.Q(department_id=work.department_id_id)
        )
        .order_by("category", "title", "version")
    )


@transaction.atomic
def assign_training_programmes(*, user, employee):
    employee = (
        Employee._base_manager.select_for_update(of=("self",))
        .select_related("employee_user_id", "employee_work_info__company_id")
        .get(pk=employee.pk)
    )
    company, onboarding = _require_training_ready(employee)
    actor = _authorize(user, "tvg_training.assign_training_programme", company=company)
    now = timezone.now()
    assignments = []
    for programme in identify_training_programmes(employee):
        assignment, _created = TrainingAssignment.all_objects.get_or_create(
            employee=employee,
            programme=programme,
            defaults={
                "onboarding": onboarding,
                "due_date": now.date()
                + timedelta(days=programme.default_deadline_days),
                "idempotency_key": (
                    f"training-assignment:{employee.pk}:{programme.public_id}:"
                    f"v{programme.version}"
                ),
                "assigned_by": actor,
                "assigned_at": now,
                "authorized_by": actor,
                "authorized_at": now,
            },
        )
        assignments.append(assignment)
        if _created:
            _event(
                company=company,
                employee=employee,
                actor=actor,
                event_type="training.assignment_authorized",
                object_id=assignment.public_id,
                programme_id=str(programme.public_id),
            )
    return assignments


def _next_attempt_sequence(assignment):
    return (
        TrainingSyncAttempt.all_objects.filter(assignment=assignment).aggregate(
            maximum=models.Max("sequence")
        )["maximum"]
        or 0
    ) + 1


def _record_attempt(
    *, assignment, operation, idempotency_key, outcome, reference="", error=""
):
    return TrainingSyncAttempt.all_objects.create(
        assignment=assignment,
        sequence=_next_attempt_sequence(assignment),
        operation=operation,
        idempotency_key=idempotency_key,
        outcome=outcome,
        provider_reference=reference[:180],
        error_message=error[:500],
    )


def _fail_assignment(assignment_id, *, operation, idempotency_key, error):
    with transaction.atomic():
        assignment = TrainingAssignment.all_objects.select_for_update(of=("self",)).get(
            pk=assignment_id
        )
        assignment.status = TrainingAssignment.Status.FAILED
        assignment.last_error = error[:500]
        assignment.save(update_fields=("status", "last_error"))
        _record_attempt(
            assignment=assignment,
            operation=operation,
            idempotency_key=idempotency_key,
            outcome=TrainingSyncAttempt.Outcome.FAILED,
            error=error,
        )
        _event(
            company=assignment.programme.company,
            employee=assignment.employee,
            event_type="training.integration_failed",
            object_id=assignment.public_id,
            operation=operation,
        )


def provision_training_assignment(*, user, assignment):
    with transaction.atomic():
        assignment = (
            TrainingAssignment.all_objects.select_for_update(of=("self",))
            .select_related(
                "employee__employee_user_id",
                "employee__employee_work_info__company_id",
                "programme__company",
                "authorized_by",
            )
            .get(pk=assignment.pk)
        )
        company, _onboarding = _require_training_ready(assignment.employee)
        actor = _authorize(user, "tvg_training.provision_training", company=company)
        if not assignment.programme.is_active or not assignment.programme.is_approved:
            raise ValidationError("An active approved programme is required.")
        if not assignment.authorized_by_id or not assignment.authorized_at:
            raise ValidationError(
                "Human authorization is required before provisioning."
            )
        if assignment.status == TrainingAssignment.Status.COMPLETED:
            return assignment
        assignment.status = TrainingAssignment.Status.PROVISIONING
        assignment.last_error = ""
        assignment.save(update_fields=("status", "last_error"))

    provider = get_training_provider()
    account_key = f"{assignment.idempotency_key}:moodle-account"
    work_email = assignment.employee.employee_work_info.email
    if not work_email:
        _fail_assignment(
            assignment.pk,
            operation="account",
            idempotency_key=account_key,
            error="A confirmed company email is required for Moodle provisioning.",
        )
        raise ValidationError("A confirmed company email is required.")
    account_request = MoodleUserRequest(
        staff_reference=assignment.employee.badge_id or str(assignment.employee.pk),
        company_reference=str(company.pk),
        company_email=work_email,
        first_name=assignment.employee.employee_first_name,
        last_name=assignment.employee.employee_last_name,
        idempotency_key=account_key,
    )
    try:
        account_result = provider.ensure_account(account_request)
        if not account_result.external_user_id or not account_result.active:
            raise TrainingProviderError(
                "Moodle returned an inactive or invalid account."
            )
    except Exception as exc:
        safe_error = (
            str(exc)
            if isinstance(exc, TrainingProviderError)
            else "Moodle account provisioning failed."
        )
        _fail_assignment(
            assignment.pk,
            operation="account",
            idempotency_key=account_key,
            error=safe_error,
        )
        raise ValidationError(safe_error) from exc

    with transaction.atomic():
        assignment = (
            TrainingAssignment.all_objects.select_for_update(of=("self",))
            .select_related(
                "employee__employee_work_info__company_id", "programme__company"
            )
            .get(pk=assignment.pk)
        )
        account, created = MoodleAccountLink.all_objects.get_or_create(
            employee=assignment.employee,
            defaults={
                "company": company,
                "external_user_id": account_result.external_user_id,
                "status": MoodleAccountLink.Status.ACTIVE,
                "last_synchronized_at": timezone.now(),
            },
        )
        if not created and account.external_user_id != account_result.external_user_id:
            raise ValidationError(
                "The employee is already linked to a different Moodle account."
            )
        if not created:
            account.status = MoodleAccountLink.Status.ACTIVE
            account.last_synchronized_at = timezone.now()
            account.save(update_fields=("status", "last_synchronized_at"))
        _record_attempt(
            assignment=assignment,
            operation="account",
            idempotency_key=account_key,
            outcome=TrainingSyncAttempt.Outcome.SUCCEEDED,
            reference=account.external_user_id,
        )

    programme_courses = list(
        TrainingProgrammeCourse.all_objects.filter(programme=assignment.programme)
        .select_related("course")
        .order_by("sequence", "pk")
    )
    if not programme_courses:
        raise ValidationError("The approved programme has no courses.")
    for programme_course in programme_courses:
        course = programme_course.course
        enrollment_key = (
            f"{assignment.idempotency_key}:course:{course.external_course_id}"
        )
        deadline_days = (
            programme_course.deadline_days or assignment.programme.default_deadline_days
        )
        due_date = assignment.assigned_at.date() + timedelta(days=deadline_days)
        request = MoodleEnrollmentRequest(
            external_user_id=account_result.external_user_id,
            external_course_id=course.external_course_id,
            due_date=due_date,
            idempotency_key=enrollment_key,
        )
        try:
            result = provider.enroll(request)
            if not result.external_enrollment_id:
                raise TrainingProviderError(
                    "Moodle returned an invalid enrollment reference."
                )
        except Exception as exc:
            safe_error = (
                str(exc)
                if isinstance(exc, TrainingProviderError)
                else "Moodle course enrollment failed."
            )
            _fail_assignment(
                assignment.pk,
                operation="enrollment",
                idempotency_key=enrollment_key,
                error=safe_error,
            )
            raise ValidationError(safe_error) from exc

        with transaction.atomic():
            locked = TrainingAssignment.all_objects.select_for_update(of=("self",)).get(
                pk=assignment.pk
            )
            peoplehub, created = PeopleHubLearningAssignment.all_objects.get_or_create(
                employee=locked.employee,
                kind=PeopleHubLearningAssignment.Kind.MOODLE,
                provider="moodle",
                external_reference=course.external_course_id,
                defaults={
                    "title": course.title,
                    "launch_url": result.launch_url or course.launch_url,
                    "due_date": due_date,
                    "assigned_by": actor,
                },
            )
            if not created and peoplehub.title != course.title:
                peoplehub.title = course.title
                peoplehub.save(update_fields=("title",))
            enrollment, created = MoodleEnrollment.all_objects.get_or_create(
                assignment=locked,
                programme_course=programme_course,
                defaults={
                    "peoplehub_assignment": peoplehub,
                    "external_enrollment_id": result.external_enrollment_id,
                    "idempotency_key": enrollment_key,
                    "due_date": due_date,
                    "last_synchronized_at": timezone.now(),
                },
            )
            if not created and (
                enrollment.external_enrollment_id != result.external_enrollment_id
            ):
                raise ValidationError(
                    "Moodle returned a conflicting enrollment reference."
                )
            _record_attempt(
                assignment=locked,
                operation="enrollment",
                idempotency_key=enrollment_key,
                outcome=TrainingSyncAttempt.Outcome.SUCCEEDED,
                reference=result.external_enrollment_id,
            )

    with transaction.atomic():
        assignment = TrainingAssignment.all_objects.select_for_update(of=("self",)).get(
            pk=assignment.pk
        )
        assignment.status = TrainingAssignment.Status.ACTIVE
        assignment.last_error = ""
        assignment.save(update_fields=("status", "last_error"))
        _event(
            company=company,
            employee=assignment.employee,
            actor=actor,
            event_type="training.provisioned",
            object_id=assignment.public_id,
            course_count=len(programme_courses),
        )
    return assignment


def _validate_progress_result(result: MoodleProgressResult):
    if not 0 <= result.progress_percent <= 100:
        raise TrainingProviderError("Moodle returned invalid progress.")
    if result.completed_at and result.progress_percent != 100:
        raise TrainingProviderError(
            "Completed Moodle training must report 100% progress."
        )


def synchronize_training_progress(*, user, assignment):
    assignment = TrainingAssignment.all_objects.select_related(
        "employee__employee_work_info__company_id", "programme__company"
    ).get(pk=assignment.pk)
    actor = _authorize(
        user, "tvg_training.provision_training", company=assignment.programme.company
    )
    _require_training_ready(assignment.employee)
    enrollments = list(
        MoodleEnrollment.all_objects.filter(assignment=assignment).select_related(
            "programme_course__course", "peoplehub_assignment"
        )
    )
    if not enrollments:
        raise ValidationError("Provision Moodle enrollments before synchronizing.")
    requests = tuple(
        MoodleProgressRequest(
            external_enrollment_id=item.external_enrollment_id,
            external_course_id=item.programme_course.course.external_course_id,
        )
        for item in enrollments
    )
    try:
        results = get_training_provider().progress(requests)
        if len(results) != len(enrollments):
            raise TrainingProviderError("Moodle returned an incomplete progress set.")
        result_by_id = {item.external_enrollment_id: item for item in results}
        if len(result_by_id) != len(enrollments) or set(result_by_id) != {
            item.external_enrollment_id for item in enrollments
        }:
            raise TrainingProviderError("Moodle returned unknown progress records.")
        for result in results:
            _validate_progress_result(result)
    except Exception as exc:
        safe_error = (
            str(exc)
            if isinstance(exc, TrainingProviderError)
            else "Moodle progress synchronization failed."
        )
        _fail_assignment(
            assignment.pk,
            operation="progress",
            idempotency_key=f"{assignment.idempotency_key}:progress",
            error=safe_error,
        )
        raise ValidationError(safe_error) from exc

    now = timezone.now()
    with transaction.atomic():
        assignment = TrainingAssignment.all_objects.select_for_update(of=("self",)).get(
            pk=assignment.pk
        )
        for enrollment in enrollments:
            result = result_by_id[enrollment.external_enrollment_id]
            enrollment = MoodleEnrollment.all_objects.select_for_update(of=("self",)).get(
                pk=enrollment.pk
            )
            enrollment.progress_percent = result.progress_percent
            enrollment.completed_at = result.completed_at
            enrollment.last_synchronized_at = now
            enrollment.last_error = ""
            enrollment.status = (
                MoodleEnrollment.Status.COMPLETED
                if result.progress_percent == 100
                else (
                    MoodleEnrollment.Status.IN_PROGRESS
                    if result.progress_percent
                    else MoodleEnrollment.Status.ENROLLED
                )
            )
            enrollment.save()
            peoplehub = enrollment.peoplehub_assignment
            peoplehub.progress_percent = result.progress_percent
            peoplehub.status = (
                PeopleHubLearningAssignment.Status.COMPLETED
                if result.progress_percent == 100
                else (
                    PeopleHubLearningAssignment.Status.IN_PROGRESS
                    if result.progress_percent
                    else PeopleHubLearningAssignment.Status.ASSIGNED
                )
            )
            peoplehub.save(update_fields=("progress_percent", "status"))
        mandatory = MoodleEnrollment.all_objects.filter(
            assignment=assignment, programme_course__mandatory=True
        )
        if (
            mandatory.exists()
            and not mandatory.exclude(status=MoodleEnrollment.Status.COMPLETED).exists()
        ):
            assignment.status = TrainingAssignment.Status.COMPLETED
            assignment.completed_at = now
        else:
            assignment.status = TrainingAssignment.Status.ACTIVE
            assignment.completed_at = None
        assignment.last_error = ""
        assignment.save(update_fields=("status", "completed_at", "last_error"))
        _record_attempt(
            assignment=assignment,
            operation="progress",
            idempotency_key=f"{assignment.idempotency_key}:progress:{now.isoformat()}",
            outcome=TrainingSyncAttempt.Outcome.SUCCEEDED,
        )
        _event(
            company=assignment.programme.company,
            employee=assignment.employee,
            actor=actor,
            event_type="training.progress_synchronized",
            object_id=assignment.public_id,
            completed=assignment.status == TrainingAssignment.Status.COMPLETED,
        )
    return assignment


def queue_overdue_training_notifications(*, today=None):
    today = today or timezone.localdate()
    created = []
    enrollments = (
        MoodleEnrollment.all_objects.filter(
            programme_course__mandatory=True,
            due_date__lt=today,
            assignment__employee__is_active=True,
        )
        .exclude(status=MoodleEnrollment.Status.COMPLETED)
        .select_related(
            "assignment__employee__employee_work_info__company_id",
            "assignment__employee__employee_work_info__reporting_manager_id",
            "assignment__employee__employee_work_info__reporting_manager_id__employee_work_info__company_id",
            "programme_course__course",
        )
    )
    for enrollment in enrollments:
        supervisor = (
            enrollment.assignment.employee.employee_work_info.reporting_manager_id
        )
        if (
            not supervisor
            or not supervisor.is_active
            or supervisor.get_company() != enrollment.assignment.employee.get_company()
        ):
            continue
        notification, was_created = TrainingNotification.all_objects.get_or_create(
            enrollment=enrollment,
            defaults={
                "supervisor": supervisor,
                "idempotency_key": f"training-overdue:{enrollment.public_id}",
            },
        )
        if was_created:
            created.append(notification)
    return created


@transaction.atomic
def link_staff_identity(*, user, employee, subject):
    employee = (
        Employee._base_manager.select_for_update(of=("self",))
        .select_related("employee_user_id", "employee_work_info__company_id")
        .get(pk=employee.pk)
    )
    company, _onboarding = _require_training_ready(employee)
    actor = _authorize(user, "tvg_training.manage_staff_identity", company=company)
    subject = subject.strip()
    if not subject or len(subject) > 180:
        raise ValidationError("A valid opaque authentik subject is required.")
    link, created = StaffIdentityLink.all_objects.get_or_create(
        employee=employee,
        defaults={"company": company, "subject": subject, "linked_by": actor},
    )
    if not created and link.subject != subject:
        raise ValidationError("This employee is linked to another authentik subject.")
    if not created and link.status != StaffIdentityLink.Status.ACTIVE:
        raise ValidationError(
            "A disabled or revoked identity cannot be silently relinked."
        )
    if created:
        _event(
            company=company,
            employee=employee,
            actor=actor,
            event_type="identity.linked",
            object_id=link.public_id,
        )
    return link


@transaction.atomic
def approve_staff_role_mapping(*, user, mapping):
    mapping = StaffRoleMapping.all_objects.select_for_update(of=("self",)).get(pk=mapping.pk)
    actor = _authorize(
        user, "tvg_training.approve_staff_role_mapping", company=mapping.company
    )
    if mapping.is_approved:
        return mapping
    mapping.approved_by = actor
    mapping.approved_at = timezone.now()
    mapping.save()
    _event(
        company=mapping.company,
        actor=actor,
        event_type="identity.role_mapping_approved",
        external_role=mapping.external_role,
        group_id=mapping.group_id,
    )
    return mapping


@dataclass(frozen=True)
class SSOAuthorization:
    employee_id: int
    company_id: int
    authorized_roles: tuple[str, ...]
    session_version: int


def authorize_authentik_login(*, user, subject, external_roles, account_enabled=True):
    if not account_enabled or not user.is_active:
        raise PermissionDenied("The staff identity is disabled.")
    employee = _employee_for_user(user)
    link = StaffIdentityLink.all_objects.filter(
        employee=employee,
        company=employee.get_company(),
        provider=StaffIdentityLink.Provider.AUTHENTIK,
        subject=subject,
        status=StaffIdentityLink.Status.ACTIVE,
    ).first()
    if not link:
        raise PermissionDenied("A pre-authorized staff identity link is required.")
    claimed_roles = {str(value) for value in external_roles}
    mappings = StaffRoleMapping.all_objects.filter(
        company=link.company,
        provider=link.provider,
        external_role__in=claimed_roles,
        approved_at__isnull=False,
        approved_by__isnull=False,
        is_active=True,
    ).select_related("group")
    locally_authorized_group_ids = set(
        CompanyGroupAssignment.objects.filter(
            user=user, company=link.company
        ).values_list("group_id", flat=True)
    )
    roles = tuple(
        sorted(
            mapping.external_role
            for mapping in mappings
            if mapping.group_id in locally_authorized_group_ids
        )
    )
    StaffIdentityLink.all_objects.filter(pk=link.pk).update(
        last_verified_at=timezone.now()
    )
    return SSOAuthorization(
        employee_id=employee.pk,
        company_id=link.company_id,
        authorized_roles=roles,
        session_version=link.session_version,
    )


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@transaction.atomic
def register_staff_session(*, user, session_key, provider_session_id=""):
    employee = _employee_for_user(user)
    identity = StaffIdentityLink.all_objects.select_for_update(of=("self",)).get(
        employee=employee, status=StaffIdentityLink.Status.ACTIVE
    )
    session = Session.objects.filter(
        session_key=session_key, expire_date__gt=timezone.now()
    ).first()
    if not session or str(session.get_decoded().get("_auth_user_id")) != str(user.pk):
        raise PermissionDenied("The local session does not belong to this staff user.")
    return StaffIdentitySession.all_objects.get_or_create(
        session_key_hash=_digest(session_key),
        defaults={
            "identity": identity,
            "provider_session_id_hash": (
                _digest(provider_session_id) if provider_session_id else ""
            ),
        },
    )[0]


@transaction.atomic
def revoke_staff_identity(*, user, identity, reason):
    identity = StaffIdentityLink.all_objects.select_for_update(of=("self",)).get(pk=identity.pk)
    actor = _authorize(
        user, "tvg_training.manage_staff_identity", company=identity.company
    )
    if not reason.strip():
        raise ValidationError("A revocation reason is required.")
    now = timezone.now()
    identity.status = StaffIdentityLink.Status.REVOKED
    identity.session_version += 1
    identity.revoked_by = actor
    identity.revoked_at = now
    identity.revocation_reason = reason.strip()[:500]
    identity.save()
    tracked = list(
        StaffIdentitySession.all_objects.filter(
            identity=identity, revoked_at__isnull=True
        )
    )
    hashes = {item.session_key_hash for item in tracked}
    session_keys = [
        item.session_key
        for item in Session.objects.filter(expire_date__gt=now).only("session_key")
        if _digest(item.session_key) in hashes
    ]
    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()
    StaffIdentitySession.all_objects.filter(
        identity=identity, revoked_at__isnull=True
    ).update(revoked_at=now)
    _event(
        company=identity.company,
        employee=identity.employee,
        actor=actor,
        event_type="identity.revoked",
        object_id=identity.public_id,
        sessions_revoked=len(session_keys),
        reason_recorded=True,
    )
    return identity
