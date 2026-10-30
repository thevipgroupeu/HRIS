from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from employee.models import Employee
from notifications.signals import notify
from project.models import Project, Task
from tvg_peoplehub.services import company_id_for, employee_for_user

from .ai import AIUnavailable, get_reporting_ai_provider
from .models import (
    AIReviewerAdjustment,
    MonthlyPresentation,
    MonthlyPresentationReview,
    MonthlyPresentationVersion,
    ReportingAIAnalysis,
    ReportingEvent,
    ReportingNotification,
    ReportingPolicy,
    ReportingRubricVersion,
    ReportStatus,
    ReviewOutcome,
    WeeklyReport,
    WeeklyReportReview,
    WeeklyReportVersion,
)


@dataclass(frozen=True)
class InternshipPeriod:
    starts_on: date
    ends_on: date


def internship_period(employee: Employee) -> InternshipPeriod:
    onboarding = getattr(employee, "tvg_intern_onboarding", None)
    if onboarding:
        starts_on = onboarding.start_date
        ends_on = onboarding.end_date
    else:
        work = employee.employee_work_info
        employee_type = getattr(work.employee_type_id, "employee_type", "")
        if "intern" not in str(employee_type).lower():
            raise ValidationError("Internship reporting is available only to interns.")
        starts_on = work.date_joining
        ends_on = work.contract_end_date
    if not starts_on or not ends_on:
        raise ValidationError("Confirmed internship dates are required.")
    return InternshipPeriod(starts_on=starts_on, ends_on=ends_on)


def reporting_policy(employee: Employee) -> ReportingPolicy:
    policy, _ = ReportingPolicy.all_objects.get_or_create(
        company_id=company_id_for(employee)
    )
    return policy


def _aware(day: date, value) -> datetime:
    return timezone.make_aware(
        datetime.combine(day, value), timezone.get_current_timezone()
    )


def weekly_deadline(employee: Employee, reporting_week: date) -> datetime:
    policy = reporting_policy(employee)
    return _aware(
        reporting_week + timedelta(days=policy.weekly_due_weekday),
        policy.weekly_due_time,
    )


def monthly_deadline(employee: Employee, presentation_month: date) -> datetime:
    policy = reporting_policy(employee)
    day = presentation_month.replace(day=policy.monthly_due_day)
    return _aware(day, policy.weekly_due_time)


def _validate_week(employee: Employee, reporting_week: date):
    if reporting_week.weekday() != 0:
        raise ValidationError(
            {"reporting_week": "The reporting week must start on Monday."}
        )
    period = internship_period(employee)
    week_end = reporting_week + timedelta(days=6)
    if week_end < period.starts_on or reporting_week > period.ends_on:
        raise ValidationError(
            {"reporting_week": "This week is outside the internship period."}
        )


def _validate_month(employee: Employee, presentation_month: date):
    if presentation_month.day != 1:
        raise ValidationError(
            {"presentation_month": "Presentation month must use the first day."}
        )
    period = internship_period(employee)
    month_end = presentation_month.replace(
        day=calendar.monthrange(presentation_month.year, presentation_month.month)[1]
    )
    if month_end < period.starts_on or presentation_month > period.ends_on:
        raise ValidationError(
            {"presentation_month": "This month is outside the internship period."}
        )


def available_projects(employee: Employee):
    company_id = company_id_for(employee)
    return (
        Project._base_manager.filter(company_id_id=company_id)
        .filter(Q(task__task_members=employee) | Q(managers=employee))
        .distinct()
        .order_by("title")
    )


def available_tasks(employee: Employee, project: Project):
    return Task._base_manager.filter(project=project, task_members=employee).order_by(
        "title"
    )


def _validate_project(employee: Employee, project: Project):
    if project.company_id_id != company_id_for(employee):
        raise PermissionDenied("The project belongs to another company.")
    if not available_projects(employee).filter(pk=project.pk).exists():
        raise ValidationError("The project is not assigned to this intern.")


def can_review(user, reviewer: Employee, subject: Employee, permission: str) -> bool:
    if company_id_for(reviewer) != company_id_for(subject):
        return False
    return bool(
        subject.employee_work_info.reporting_manager_id_id == reviewer.pk
        or user.is_superuser
        or user.has_perm(permission)
    )


def can_view_item(user, viewer: Employee, subject: Employee) -> bool:
    if company_id_for(viewer) != company_id_for(subject):
        return False
    return bool(
        viewer.pk == subject.pk
        or subject.employee_work_info.reporting_manager_id_id == viewer.pk
        or user.is_superuser
        or user.has_perm("tvg_reporting.view_management_reporting")
    )


def _require_owner(actor: Employee, subject: Employee):
    if actor.pk != subject.pk:
        raise PermissionDenied("Only the intern can edit or submit this item.")


def _event(*, employee, actor, event_type, item, version=None, metadata=None):
    return ReportingEvent.all_objects.create(
        employee=employee,
        actor=actor,
        event_type=event_type,
        object_type=(
            "weekly_report" if isinstance(item, WeeklyReport) else "presentation"
        ),
        object_public_id=item.public_id,
        version_number=version,
        metadata=metadata or {},
    )


def _schedule(*, recipient, kind, item, scheduled_for, suffix):
    target = (
        {"weekly_report": item}
        if isinstance(item, WeeklyReport)
        else {"presentation": item}
    )
    return ReportingNotification.all_objects.get_or_create(
        dedupe_key=f"{kind}:{item.public_id}:{suffix}:{recipient.pk}",
        defaults={
            "recipient": recipient,
            "kind": kind,
            "scheduled_for": scheduled_for,
            **target,
        },
    )[0]


def _schedule_weekly_reminders(report: WeeklyReport):
    policy = reporting_policy(report.employee)
    _schedule(
        recipient=report.employee,
        kind=ReportingNotification.Kind.WEEKLY_REMINDER,
        item=report,
        scheduled_for=report.deadline_at
        - timedelta(hours=policy.weekly_reminder_hours_before),
        suffix="deadline",
    )
    _schedule(
        recipient=report.employee,
        kind=ReportingNotification.Kind.WEEKLY_OVERDUE,
        item=report,
        scheduled_for=report.deadline_at + timedelta(minutes=1),
        suffix="overdue",
    )


def _schedule_monthly_reminder(presentation: MonthlyPresentation):
    policy = reporting_policy(presentation.employee)
    _schedule(
        recipient=presentation.employee,
        kind=ReportingNotification.Kind.MONTHLY_REMINDER,
        item=presentation,
        scheduled_for=presentation.deadline_at
        - timedelta(days=policy.monthly_reminder_days_before),
        suffix="deadline",
    )


@transaction.atomic
def save_weekly_draft(
    *,
    employee: Employee,
    reporting_week: date,
    assigned_project: Project,
    planned_objectives: str,
    completed_tasks: str,
    evidence_links: list[str],
    blockers: str,
    learning_outcomes: str,
    next_week_plan: str,
    hours: Decimal | None,
    progress_percent: int | None,
    report: WeeklyReport | None = None,
):
    _validate_week(employee, reporting_week)
    _validate_project(employee, assigned_project)
    for link in evidence_links:
        if not link.lower().startswith("https://"):
            raise ValidationError({"evidence_links": "Evidence links must use HTTPS."})
        URLValidator()(link)
    if report:
        report = WeeklyReport.all_objects.select_for_update(of=("self",)).get(pk=report.pk)
        _require_owner(employee, report.employee)
        if report.status not in {ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED}:
            raise ValidationError("Submitted reports cannot be edited.")
    else:
        report = WeeklyReport(employee=employee)
    report.reporting_week = reporting_week
    report.assigned_project = assigned_project
    report.planned_objectives = planned_objectives
    report.completed_tasks = completed_tasks
    report.evidence_links = evidence_links
    report.blockers = blockers
    report.learning_outcomes = learning_outcomes
    report.next_week_plan = next_week_plan
    report.hours = hours
    report.progress_percent = progress_percent
    report.deadline_at = weekly_deadline(employee, reporting_week)
    report.save()
    _schedule_weekly_reminders(report)
    _event(
        employee=employee, actor=employee, event_type="weekly.draft_saved", item=report
    )
    return report


def _weekly_snapshot(report: WeeklyReport, tasks) -> dict:
    return {
        "reporting_week": report.reporting_week.isoformat(),
        "assigned_project_id": report.assigned_project_id,
        "assigned_project": report.assigned_project.title,
        "planned_objectives": report.planned_objectives,
        "completed_tasks": report.completed_tasks,
        "completed_task_records": [
            {"id": task.pk, "title": task.title} for task in tasks
        ],
        "evidence_links": report.evidence_links,
        "blockers": report.blockers,
        "learning_outcomes": report.learning_outcomes,
        "next_week_plan": report.next_week_plan,
        "hours": str(report.hours) if report.hours is not None else None,
        "progress_percent": report.progress_percent,
        "deadline_at": report.deadline_at.isoformat(),
    }


@transaction.atomic
def submit_weekly_report(*, employee: Employee, report: WeeklyReport, task_ids=()):
    report = (
        WeeklyReport.all_objects.select_for_update(of=("self",))
        .select_related("employee", "assigned_project")
        .get(pk=report.pk)
    )
    _require_owner(employee, report.employee)
    if report.status not in {ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED}:
        raise ValidationError(
            "Only draft or revision-required reports can be submitted."
        )
    required = (
        report.planned_objectives,
        report.completed_tasks,
        report.learning_outcomes,
        report.next_week_plan,
    )
    if any(not str(value).strip() for value in required):
        raise ValidationError("All required weekly report sections must be completed.")
    _validate_week(employee, report.reporting_week)
    _validate_project(employee, report.assigned_project)
    tasks = list(
        available_tasks(employee, report.assigned_project).filter(pk__in=task_ids)
    )
    if len(tasks) != len(set(map(int, task_ids))):
        raise ValidationError("Completed tasks must be assigned native project tasks.")
    report.completed_task_records.set(tasks)
    version_number = report.current_version + 1
    version = WeeklyReportVersion.all_objects.create(
        report=report,
        version_number=version_number,
        content=_weekly_snapshot(report, tasks),
        submitted_by=employee,
    )
    report.status = ReportStatus.SUBMITTED
    report.current_version = version_number
    report.submitted_at = version.submitted_at
    report.approved_at = None
    report.save()
    ReportingNotification.all_objects.filter(
        weekly_report=report,
        kind__in=(
            ReportingNotification.Kind.WEEKLY_REMINDER,
            ReportingNotification.Kind.WEEKLY_OVERDUE,
        ),
        status=ReportingNotification.Status.PENDING,
    ).update(status=ReportingNotification.Status.CANCELED)
    supervisor = employee.employee_work_info.reporting_manager_id
    if supervisor:
        _schedule(
            recipient=supervisor,
            kind=ReportingNotification.Kind.REVIEW_REQUIRED,
            item=report,
            scheduled_for=timezone.now(),
            suffix=f"v{version_number}",
        )
    _event(
        employee=employee,
        actor=employee,
        event_type="weekly.submitted",
        item=report,
        version=version_number,
    )
    return version


@transaction.atomic
def save_monthly_draft(
    *,
    employee: Employee,
    presentation_month: date,
    assigned_project: Project,
    presentation_date: date,
    project_milestones: str,
    file_metadata: dict | None = None,
    presentation: MonthlyPresentation | None = None,
):
    _validate_month(employee, presentation_month)
    _validate_project(employee, assigned_project)
    if presentation:
        presentation = MonthlyPresentation.all_objects.select_for_update(of=("self",)).get(
            pk=presentation.pk
        )
        _require_owner(employee, presentation.employee)
        if presentation.status not in {
            ReportStatus.DRAFT,
            ReportStatus.REVISION_REQUIRED,
        }:
            raise ValidationError("Submitted presentations cannot be edited.")
    else:
        presentation = MonthlyPresentation(employee=employee)
    presentation.presentation_month = presentation_month
    presentation.assigned_project = assigned_project
    presentation.presentation_date = presentation_date
    presentation.project_milestones = project_milestones
    presentation.deadline_at = monthly_deadline(employee, presentation_month)
    if file_metadata:
        presentation.presentation_file = file_metadata["file"]
        presentation.original_filename = file_metadata["original_filename"]
        presentation.content_type = file_metadata["content_type"]
        presentation.sha256 = file_metadata["sha256"]
        presentation.size_bytes = file_metadata["size_bytes"]
    presentation.save()
    _schedule_monthly_reminder(presentation)
    _event(
        employee=employee,
        actor=employee,
        event_type="presentation.draft_saved",
        item=presentation,
    )
    return presentation


def _presentation_snapshot(presentation: MonthlyPresentation) -> dict:
    return {
        "presentation_month": presentation.presentation_month.isoformat(),
        "assigned_project_id": presentation.assigned_project_id,
        "assigned_project": presentation.assigned_project.title,
        "presentation_date": presentation.presentation_date.isoformat(),
        "project_milestones": presentation.project_milestones,
        "original_filename": presentation.original_filename,
        "content_type": presentation.content_type,
        "sha256": presentation.sha256,
        "size_bytes": presentation.size_bytes,
        "deadline_at": presentation.deadline_at.isoformat(),
    }


@transaction.atomic
def submit_monthly_presentation(*, employee: Employee, presentation):
    presentation = (
        MonthlyPresentation.all_objects.select_for_update(of=("self",))
        .select_related("employee", "assigned_project")
        .get(pk=presentation.pk)
    )
    _require_owner(employee, presentation.employee)
    if presentation.status not in {ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED}:
        raise ValidationError(
            "Only draft or revision-required presentations can be submitted."
        )
    if (
        not presentation.presentation_file
        or not presentation.project_milestones.strip()
    ):
        raise ValidationError("A validated file and project milestones are required.")
    _validate_month(employee, presentation.presentation_month)
    _validate_project(employee, presentation.assigned_project)
    version_number = presentation.current_version + 1
    version = MonthlyPresentationVersion.all_objects.create(
        presentation=presentation,
        version_number=version_number,
        presentation_file=presentation.presentation_file.name,
        original_filename=presentation.original_filename,
        content_type=presentation.content_type,
        sha256=presentation.sha256,
        size_bytes=presentation.size_bytes,
        content=_presentation_snapshot(presentation),
        submitted_by=employee,
    )
    presentation.status = ReportStatus.SUBMITTED
    presentation.current_version = version_number
    presentation.submitted_at = version.submitted_at
    presentation.approved_at = None
    presentation.save()
    ReportingNotification.all_objects.filter(
        presentation=presentation,
        kind=ReportingNotification.Kind.MONTHLY_REMINDER,
        status=ReportingNotification.Status.PENDING,
    ).update(status=ReportingNotification.Status.CANCELED)
    supervisor = employee.employee_work_info.reporting_manager_id
    if supervisor:
        _schedule(
            recipient=supervisor,
            kind=ReportingNotification.Kind.REVIEW_REQUIRED,
            item=presentation,
            scheduled_for=timezone.now(),
            suffix=f"v{version_number}",
        )
    _event(
        employee=employee,
        actor=employee,
        event_type="presentation.submitted",
        item=presentation,
        version=version_number,
    )
    return version


@transaction.atomic
def approve_rubric(*, user, rubric: ReportingRubricVersion):
    actor = employee_for_user(user)
    rubric = ReportingRubricVersion.all_objects.select_for_update(of=("self",)).get(pk=rubric.pk)
    if rubric.company_id != company_id_for(actor):
        raise PermissionDenied("The rubric belongs to another company.")
    if not (
        user.is_superuser or user.has_perm("tvg_reporting.approve_reporting_rubric")
    ):
        raise PermissionDenied("Rubric approval permission is required.")
    if not rubric.approved_at:
        rubric.approved_by = actor
        rubric.approved_at = timezone.now()
        rubric.save()
    return rubric


def _validate_review(*, rubric, scores, adjustments, adjustment_reason, company_id):
    if rubric.company_id != company_id or not rubric.approved_at:
        raise ValidationError("An approved same-company rubric version is required.")
    expected = {item["code"]: item for item in rubric.criteria}
    if set(scores) != set(expected):
        raise ValidationError("Every rubric criterion requires exactly one score.")
    for code, score in scores.items():
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValidationError(f"Rubric score {code} must be numeric.")
        if score < 0 or score > expected[code]["max_score"]:
            raise ValidationError(f"Rubric score {code} is outside its allowed range.")
    if adjustments and not adjustment_reason.strip():
        raise ValidationError("Reviewer adjustments require an explanation.")


def _complete_review_notifications(*, item, employee, version_number):
    filters = (
        {"weekly_report": item}
        if isinstance(item, WeeklyReport)
        else {"presentation": item}
    )
    ReportingNotification.all_objects.filter(
        **filters,
        kind=ReportingNotification.Kind.REVIEW_REQUIRED,
        status=ReportingNotification.Status.PENDING,
    ).update(status=ReportingNotification.Status.CANCELED)
    _schedule(
        recipient=employee,
        kind=ReportingNotification.Kind.REVIEW_COMPLETED,
        item=item,
        scheduled_for=timezone.now(),
        suffix=f"v{version_number}",
    )


@transaction.atomic
def review_weekly_report(
    *,
    user,
    version,
    outcome,
    comments,
    task_completion_evidence="",
    development_feedback="",
    writing_quality_comments="",
    internship_performance_comments="",
    rubric,
    rubric_scores,
    reviewer_adjustments=None,
    adjustment_reason="",
):
    reviewer = employee_for_user(user)
    version = (
        WeeklyReportVersion.all_objects.select_for_update(of=("self",))
        .select_related("report__employee__employee_work_info")
        .get(pk=version.pk)
    )
    report = version.report
    if not can_review(
        user, reviewer, report.employee, "tvg_reporting.review_weekly_report"
    ):
        raise PermissionDenied("Supervisor review access is required.")
    if (
        report.status != ReportStatus.SUBMITTED
        or report.current_version != version.version_number
    ):
        raise ValidationError("Only the current submitted version can be reviewed.")
    if outcome not in ReviewOutcome.values or not comments.strip():
        raise ValidationError("A valid outcome and reviewer comments are required.")
    reviewer_adjustments = reviewer_adjustments or {}
    _validate_review(
        rubric=rubric,
        scores=rubric_scores,
        adjustments=reviewer_adjustments,
        adjustment_reason=adjustment_reason,
        company_id=company_id_for(reviewer),
    )
    review = WeeklyReportReview.all_objects.create(
        version=version,
        reviewer=reviewer,
        outcome=outcome,
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
    report.status = outcome
    report.approved_at = (
        review.reviewed_at if outcome == ReviewOutcome.APPROVED else None
    )
    report.save()
    _complete_review_notifications(
        item=report, employee=report.employee, version_number=version.version_number
    )
    _event(
        employee=report.employee,
        actor=reviewer,
        event_type=f"weekly.{outcome}",
        item=report,
        version=version.version_number,
        metadata={"rubric_public_id": str(rubric.public_id)},
    )
    return review


@transaction.atomic
def review_monthly_presentation(
    *,
    user,
    version,
    outcome,
    comments,
    milestone_evidence="",
    development_feedback="",
    writing_quality_comments="",
    internship_performance_comments="",
    rubric,
    rubric_scores,
    reviewer_adjustments=None,
    adjustment_reason="",
):
    reviewer = employee_for_user(user)
    version = (
        MonthlyPresentationVersion.all_objects.select_for_update(of=("self",))
        .select_related("presentation__employee__employee_work_info")
        .get(pk=version.pk)
    )
    presentation = version.presentation
    if not can_review(
        user,
        reviewer,
        presentation.employee,
        "tvg_reporting.review_monthly_presentation",
    ):
        raise PermissionDenied("Supervisor review access is required.")
    if (
        presentation.status != ReportStatus.SUBMITTED
        or presentation.current_version != version.version_number
    ):
        raise ValidationError("Only the current submitted version can be reviewed.")
    if outcome not in ReviewOutcome.values or not comments.strip():
        raise ValidationError("A valid outcome and reviewer comments are required.")
    reviewer_adjustments = reviewer_adjustments or {}
    _validate_review(
        rubric=rubric,
        scores=rubric_scores,
        adjustments=reviewer_adjustments,
        adjustment_reason=adjustment_reason,
        company_id=company_id_for(reviewer),
    )
    review = MonthlyPresentationReview.all_objects.create(
        version=version,
        reviewer=reviewer,
        outcome=outcome,
        comments=comments,
        milestone_evidence=milestone_evidence,
        development_feedback=development_feedback,
        writing_quality_comments=writing_quality_comments,
        internship_performance_comments=internship_performance_comments,
        rubric=rubric,
        rubric_scores=rubric_scores,
        reviewer_adjustments=reviewer_adjustments,
        adjustment_reason=adjustment_reason,
    )
    presentation.status = outcome
    presentation.approved_at = (
        review.reviewed_at if outcome == ReviewOutcome.APPROVED else None
    )
    presentation.save()
    _complete_review_notifications(
        item=presentation,
        employee=presentation.employee,
        version_number=version.version_number,
    )
    _event(
        employee=presentation.employee,
        actor=reviewer,
        event_type=f"presentation.{outcome}",
        item=presentation,
        version=version.version_number,
        metadata={"rubric_public_id": str(rubric.public_id)},
    )
    return review


def _ai_target_and_content(*, weekly_version=None, presentation_version=None):
    if bool(weekly_version) == bool(presentation_version):
        raise ValidationError("Select exactly one submitted reporting version.")
    target = weekly_version or presentation_version
    subject = target.report.employee if weekly_version else target.presentation.employee
    return target, subject, target.content


@transaction.atomic
def request_ai_analysis(
    *, user, purpose, weekly_version=None, presentation_version=None
):
    requester = employee_for_user(user)
    target, subject, content = _ai_target_and_content(
        weekly_version=weekly_version, presentation_version=presentation_version
    )
    if not can_view_item(user, requester, subject):
        raise PermissionDenied("You cannot analyze this reporting item.")
    if purpose not in ReportingAIAnalysis.Purpose.values:
        raise ValidationError("Unknown AI-support purpose.")
    provider = get_reporting_ai_provider()
    analysis = ReportingAIAnalysis.objects.create(
        weekly_version=weekly_version,
        presentation_version=presentation_version,
        purpose=purpose,
        provider=provider.provider_name,
        prompt_version="reporting-v1",
        status=ReportingAIAnalysis.Status.REQUESTED,
        requested_by=requester,
    )
    try:
        result = provider.analyze(content=content, purpose=purpose)
    except AIUnavailable:
        analysis.status = ReportingAIAnalysis.Status.UNAVAILABLE
        analysis.completed_at = timezone.now()
        analysis.save(update_fields=("status", "completed_at"))
        return analysis
    except Exception:
        analysis.status = ReportingAIAnalysis.Status.FAILED
        analysis.completed_at = timezone.now()
        analysis.save(update_fields=("status", "completed_at"))
        return analysis
    analysis.model_version = result.model_version
    analysis.result = {
        "summary": result.summary,
        "missing_evidence": list(result.missing_evidence),
        "supervisor_briefing": result.supervisor_briefing,
        "decision": None,
    }
    analysis.status = ReportingAIAnalysis.Status.COMPLETED
    analysis.completed_at = timezone.now()
    analysis.save(update_fields=("model_version", "result", "status", "completed_at"))
    return analysis


def record_ai_adjustment(*, user, analysis, adjusted_result, reason):
    reviewer = employee_for_user(user)
    target, subject, _ = _ai_target_and_content(
        weekly_version=analysis.weekly_version,
        presentation_version=analysis.presentation_version,
    )
    del target
    if not can_view_item(user, reviewer, subject):
        raise PermissionDenied("You cannot adjust this analysis.")
    if not reason.strip():
        raise ValidationError("An adjustment reason is required.")
    return AIReviewerAdjustment.objects.create(
        analysis=analysis,
        reviewer=reviewer,
        adjusted_result=adjusted_result,
        reason=reason,
    )


def _notification_verb(notification):
    return {
        ReportingNotification.Kind.WEEKLY_REMINDER: "Weekly report deadline approaching",
        ReportingNotification.Kind.WEEKLY_OVERDUE: "Weekly report is overdue",
        ReportingNotification.Kind.MONTHLY_REMINDER: "Monthly presentation deadline approaching",
        ReportingNotification.Kind.REVIEW_REQUIRED: "Internship report requires your review",
        ReportingNotification.Kind.REVIEW_COMPLETED: "Supervisor feedback is available",
    }[notification.kind]


@transaction.atomic
def dispatch_due_notifications(*, as_of=None):
    as_of = as_of or timezone.now()
    queued = list(
        ReportingNotification.all_objects.select_for_update(of=("self",))
        .select_related(
            "recipient__employee_user_id",
            "weekly_report__employee",
            "presentation__employee",
        )
        .filter(status=ReportingNotification.Status.PENDING, scheduled_for__lte=as_of)
    )
    delivered = []
    for notification in queued:
        target = notification.weekly_report or notification.presentation
        if notification.kind in {
            ReportingNotification.Kind.WEEKLY_REMINDER,
            ReportingNotification.Kind.WEEKLY_OVERDUE,
            ReportingNotification.Kind.MONTHLY_REMINDER,
        } and target.status not in {ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED}:
            notification.status = ReportingNotification.Status.CANCELED
            notification.save(update_fields=("status",))
            continue
        recipient_user = notification.recipient.employee_user_id
        if not recipient_user:
            continue
        actor = target.employee
        responses = notify.send(
            actor,
            recipient=recipient_user,
            verb=_notification_verb(notification),
            target=target,
            public=False,
            redirect="/reporting/",
        )
        created = next((items[0] for _, items in responses if items), None)
        notification.native_notification_id = getattr(created, "pk", None)
        notification.status = ReportingNotification.Status.DELIVERED
        notification.delivered_at = as_of
        notification.save(
            update_fields=("native_notification_id", "status", "delivered_at")
        )
        delivered.append(notification)
    return delivered


def management_summary(*, user):
    viewer = employee_for_user(user)
    if not (
        user.is_superuser or user.has_perm("tvg_reporting.view_management_reporting")
    ):
        raise PermissionDenied("Management reporting permission is required.")
    company_id = company_id_for(viewer)
    weekly = WeeklyReport.all_objects.filter(
        employee__employee_work_info__company_id_id=company_id
    )
    monthly = MonthlyPresentation.all_objects.filter(
        employee__employee_work_info__company_id_id=company_id
    )
    return {
        "weekly_counts": list(
            weekly.values("status").annotate(total=Count("id")).order_by("status")
        ),
        "monthly_counts": list(
            monthly.values("status").annotate(total=Count("id")).order_by("status")
        ),
        "overdue_weekly": weekly.filter(
            deadline_at__lt=timezone.now(),
            status__in=(ReportStatus.DRAFT, ReportStatus.REVISION_REQUIRED),
        ).count(),
        "interns": Employee._base_manager.filter(
            employee_work_info__company_id_id=company_id,
            employee_work_info__employee_type_id__employee_type__icontains="intern",
            is_active=True,
        ).count(),
    }
