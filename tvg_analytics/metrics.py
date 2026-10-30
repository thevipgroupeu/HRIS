"""Aggregate PeopleOps metric collection.

Every query uses the unscoped ``all_objects``/``_base_manager`` plus an explicit
company filter, so results never depend on the Horilla session company. Only
counts, rates, durations and allow-listed dimension labels leave this module;
no person-level field, document, free text or protected characteristic is
read. See ``definitions.METRICS`` for the meaning of every key.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Min, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from asset.models import AssetAssignment
from tvg_automation.models import DeliveryAttempt, LifecycleEvent, WorkflowExecution
from tvg_careers.models import Application
from tvg_erasmus.models import (
    AcceptanceLetter,
    ErasmusCase,
    ErasmusCaseDocument,
    ErasmusCaseEvent,
    ScreeningDecision,
    ScreeningEvent,
    VerificationCase,
)
from tvg_interviews.models import (
    DecisionNotificationTemplate,
    InterviewBooking,
    InterviewDecision,
)
from tvg_offboarding.models import AccessRevocationTask, CompletionCase
from tvg_onboarding.models import (
    AccountProvisioningRequest,
    InternOnboarding,
    OnboardingChecklistLink,
)
from tvg_reporting.models import (
    MonthlyPresentation,
    ReportStatus,
    WeeklyReport,
    WeeklyReportVersion,
)
from tvg_training.models import TrainingAssignment

from .definitions import (
    HORIZON_DAYS,
    METRICS,
    RECRUITMENT_BACKLOG_STAGES,
    RECRUITMENT_DURATION_STAGES,
)

UNASSIGNED = "(unassigned)"
DATES_CONFIRMED_EVENT = "erasmus.dates_confirmed"
OPEN_SCREENING_STATUSES = (
    ScreeningDecision.Status.INITIAL_SCREENING,
    ScreeningDecision.Status.HOLD,
    ScreeningDecision.Status.INFORMATION_REQUESTED,
)
OPEN_VERIFICATION_STATUSES = (
    VerificationCase.Status.REQUESTED,
    VerificationCase.Status.SUBMITTED,
    VerificationCase.Status.UNDER_REVIEW,
    VerificationCase.Status.REVISION_NEEDED,
)


@dataclass(frozen=True)
class AnalyticsQuery:
    company_id: int
    period_start: date
    period_end: date
    as_of_date: date
    venture_id: int | None = None
    department_id: int | None = None
    role_id: int | None = None
    source: str = ""

    @property
    def cutoff(self) -> date:
        """Last day an event can count: the period end, never after as-of."""

        return min(self.period_end, self.as_of_date)

    @property
    def has_narrowing_filter(self) -> bool:
        return bool(
            self.venture_id or self.department_id or self.role_id or self.source
        )

    @property
    def filter_dict(self):
        return {
            key: value
            for key, value in {
                "venture_id": self.venture_id,
                "department_id": self.department_id,
                "role_id": self.role_id,
                "source": self.source,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
            }.items()
            if value not in (None, "")
        }


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(round(value, 4)))
    return Decimal(value or 0)


def _round2(value) -> Decimal:
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _point(key, value, dimensions=None, numerator=None, denominator=None):
    return {
        "dashboard": METRICS[key].dashboard,
        "metric_key": key,
        "value": _decimal(value),
        "dimensions": dimensions or {},
        "numerator": _decimal(numerator) if numerator is not None else None,
        "denominator": _decimal(denominator) if denominator is not None else None,
    }


def _rate_point(key, numerator, denominator):
    value = _round2(Decimal(numerator) * 100 / denominator) if denominator else 0
    return _point(key, value, numerator=numerator, denominator=denominator)


def _local_date(value: datetime | None) -> date | None:
    return timezone.localtime(value).date() if value else None


def _in_period(field, query):
    return {
        f"{field}__date__gte": query.period_start,
        f"{field}__date__lte": query.period_end,
    }


def _date_in_period(value, query) -> bool:
    return value is not None and query.period_start <= value <= query.period_end


def _label(value):
    return value if value not in (None, "") else UNASSIGNED


def _grouped(queryset, id_field, label_field, key, metric):
    rows = queryset.order_by().values(id_field, label_field).annotate(total=Count("pk"))
    return [
        _point(
            metric,
            row["total"],
            {key: _label(row[label_field]), f"{key}_id": row[id_field]},
        )
        for row in sorted(
            rows, key=lambda row: (str(row[label_field]), row[id_field] or 0)
        )
    ]


def _by_value(queryset, field, dimension, metric, distinct_field=None):
    total = Count(distinct_field, distinct=True) if distinct_field else Count("pk")
    rows = queryset.order_by().values(field).annotate(total=total)
    return [
        _point(metric, row["total"], {dimension: _label(row[field])})
        for row in sorted(rows, key=lambda row: str(row[field]))
    ]


def _duration_point(stage, pairs):
    days = []
    for start, end in pairs:
        if start and end and end >= start:
            days.append((end - start).total_seconds() / 86400)
    total = Decimal(str(round(sum(days), 4)))
    count = len(days)
    value = _round2(total / count) if count else 0
    return _point(
        "stage_average_duration_days",
        value,
        {"stage": stage},
        numerator=total,
        denominator=count,
    )


def application_scope(query, *, enforce_recruitment_company=True):
    """Applications of the company, narrowed by the recruitment filters.

    Department and role come from the opening's native Horilla job position.
    """

    qs = Application.all_objects.filter(
        opening__venture__legal_employer_id=query.company_id
    )
    if enforce_recruitment_company:
        qs = qs.filter(opening__recruitment__company_id=query.company_id)
    if query.venture_id:
        qs = qs.filter(opening__venture_id=query.venture_id)
    if query.department_id:
        qs = qs.filter(
            opening__recruitment__job_position_id__department_id=query.department_id
        )
    if query.role_id:
        qs = qs.filter(opening__recruitment__job_position_id_id=query.role_id)
    if query.source:
        qs = qs.filter(source=query.source)
    return qs


def onboarding_scope(query):
    """Intern onboardings of the company, narrowed by the internship filters.

    Venture and department come from the onboarding itself (where the intern
    works); role and source come from the originating application.
    """

    qs = InternOnboarding.all_objects.filter(
        venture__legal_employer_id=query.company_id
    )
    if query.venture_id:
        qs = qs.filter(venture_id=query.venture_id)
    if query.department_id:
        qs = qs.filter(department_id=query.department_id)
    if query.role_id:
        qs = qs.filter(
            erasmus_case__application__opening__recruitment__job_position_id_id=(
                query.role_id
            )
        )
    if query.source:
        qs = qs.filter(erasmus_case__application__source=query.source)
    return qs


def period_applications(query, **scope_options):
    return application_scope(query, **scope_options).filter(
        **_in_period("submitted_at", query)
    )


def _recruitment_volume(query, scope):
    period_apps = scope.filter(**_in_period("submitted_at", query))
    points = [
        _point("applications_total", period_apps.count()),
        _point(
            "unique_persons",
            period_apps.order_by().values("person_id").distinct().count(),
        ),
        _point(
            "applications_withdrawn",
            period_apps.filter(
                withdrawn_at__isnull=False,
                withdrawn_at__date__lte=query.as_of_date,
            ).count(),
        ),
    ]
    points += _by_value(period_apps, "source", "source", "applications_by_source")
    points += _grouped(
        period_apps,
        "opening__venture_id",
        "opening__venture__name",
        "venture",
        "applications_by_venture",
    )
    points += _grouped(
        period_apps,
        "opening__recruitment__job_position_id__department_id",
        "opening__recruitment__job_position_id__department_id__department",
        "department",
        "applications_by_department",
    )
    points += _grouped(
        period_apps,
        "opening__recruitment__job_position_id",
        "opening__recruitment__job_position_id__job_position",
        "role",
        "applications_by_role",
    )
    months = Counter(
        _local_date(submitted_at).replace(day=1).isoformat()
        for submitted_at in period_apps.order_by().values_list(
            "submitted_at", flat=True
        )
    )
    points += [
        _point("applications_by_month", total, {"month": month})
        for month, total in sorted(months.items())
    ]
    return points


def _screening_and_verification(query, scope):
    decisions = ScreeningEvent.all_objects.filter(
        screening__application__in=scope
    ).exclude(to_status=ScreeningDecision.Status.INITIAL_SCREENING)
    in_period = decisions.filter(**_in_period("occurred_at", query))
    points = [
        _point(
            "applications_screened",
            in_period.order_by().values("screening__application_id").distinct().count(),
        )
    ]
    points += _by_value(
        in_period,
        "to_status",
        "screening_outcome",
        "initial_screening_outcomes",
        distinct_field="screening__application_id",
    )
    requested = (
        VerificationCase.all_objects.filter(application__in=scope)
        .exclude(status=VerificationCase.Status.NOT_REQUIRED)
        .filter(requested_at__isnull=False, **_in_period("requested_at", query))
    )
    requested_count = requested.count()
    verified_count = requested.filter(
        status=VerificationCase.Status.VERIFIED,
        completed_at__isnull=False,
        completed_at__date__lte=query.as_of_date,
    ).count()
    points += [
        _point("verification_requested", requested_count),
        _rate_point("verification_completion_rate", verified_count, requested_count),
    ]
    return points


def _latest_decisions(query, scope):
    """Latest human decision per application on or before the as-of date."""

    latest = {}
    rows = (
        InterviewDecision.all_objects.filter(
            booking__invitation__application__in=scope,
            decided_at__date__lte=query.as_of_date,
        )
        .order_by()
        .values_list(
            "booking__invitation__application_id", "decided_at", "pk", "outcome"
        )
    )
    for application_id, decided_at, pk, outcome in rows:
        current = latest.get(application_id)
        if current is None or (decided_at, pk) > current[:2]:
            latest[application_id] = (decided_at, pk, outcome)
    return latest


def _interviews_and_decisions(query, scope):
    bookings = InterviewBooking.all_objects.filter(invitation__application__in=scope)
    completed = bookings.filter(
        status=InterviewBooking.Status.COMPLETED,
        completed_at__isnull=False,
        **_in_period("completed_at", query),
    )
    outcomes = Counter(
        outcome
        for decided_at, _pk, outcome in _latest_decisions(query, scope).values()
        if _date_in_period(_local_date(decided_at), query)
    )
    Outcome = DecisionNotificationTemplate.Outcome
    return [
        _point(
            "interviews_total",
            bookings.exclude(status=InterviewBooking.Status.CANCELLED)
            .filter(**_in_period("booked_at", query))
            .count(),
        ),
        _point("interviews_completed", completed.count()),
        _point(
            "applications_interviewed",
            completed.order_by()
            .values("invitation__application_id")
            .distinct()
            .count(),
        ),
        _point("selected_candidates", outcomes[Outcome.SELECTED]),
        _point("rejected_candidates", outcomes[Outcome.REJECTED]),
        _point("on_hold_candidates", outcomes[Outcome.HOLD]),
    ]


def _first_dates_confirmed(cases):
    return {
        row["case_id"]: row["first"]
        for row in ErasmusCaseEvent.all_objects.filter(
            case__in=cases, event_type=DATES_CONFIRMED_EVENT
        )
        .order_by()
        .values("case_id")
        .annotate(first=Min("occurred_at"))
    }


def _offers_and_acceptance(query, scope):
    cases = ErasmusCase.all_objects.filter(application__in=scope)
    confirmed = _first_dates_confirmed(cases)
    firsts = (
        AcceptanceLetter.all_objects.filter(case__in=cases)
        .order_by()
        .values("case_id")
        .annotate(first_issue=Min("issue_date"), first_received=Min("received_at"))
    )
    offers = accepted = 0
    for row in firsts:
        offers += _date_in_period(row["first_issue"], query)
        accepted += _date_in_period(_local_date(row["first_received"]), query)
    return [
        _point(
            "date_confirmations",
            sum(_date_in_period(_local_date(at), query) for at in confirmed.values()),
        ),
        _point("offers_issued", offers),
        _point("accepted_candidates", accepted),
    ]


def _stage_durations(query, scope):
    cases = ErasmusCase.all_objects.filter(application__in=scope)
    first_screening = (
        ScreeningEvent.all_objects.filter(screening__application__in=scope)
        .exclude(to_status=ScreeningDecision.Status.INITIAL_SCREENING)
        .order_by()
        .values("screening__application_id", "screening__application__submitted_at")
        .annotate(first=Min("occurred_at"))
    )
    confirmed = _first_dates_confirmed(cases)
    selection_times = dict(
        cases.filter(pk__in=list(confirmed))
        .order_by()
        .values_list("pk", "selection_decision__decided_at")
    )
    pairs = {
        "application_to_first_screening": [
            (row["screening__application__submitted_at"], row["first"])
            for row in first_screening
            if _date_in_period(_local_date(row["first"]), query)
        ],
        "verification_request_to_completion": list(
            VerificationCase.all_objects.filter(
                application__in=scope,
                status=VerificationCase.Status.VERIFIED,
                requested_at__isnull=False,
                completed_at__isnull=False,
                **_in_period("completed_at", query),
            ).values_list("requested_at", "completed_at")
        ),
        "invitation_to_booking": list(
            InterviewBooking.all_objects.filter(
                invitation__application__in=scope, **_in_period("booked_at", query)
            ).values_list("invitation__invited_at", "booked_at")
        ),
        "interview_to_decision": list(
            InterviewDecision.all_objects.filter(
                booking__invitation__application__in=scope,
                booking__completed_at__isnull=False,
                **_in_period("decided_at", query),
            ).values_list("booking__completed_at", "decided_at")
        ),
        "selection_to_date_confirmation": [
            (selection_times.get(case_id), first)
            for case_id, first in confirmed.items()
            if _date_in_period(_local_date(first), query)
        ],
        "letter_sent_to_receipt": list(
            AcceptanceLetter.all_objects.filter(
                case__in=cases,
                sent_at__isnull=False,
                received_at__isnull=False,
                **_in_period("received_at", query),
            ).values_list("sent_at", "received_at")
        ),
    }
    return [
        _duration_point(stage, pairs[stage]) for stage in RECRUITMENT_DURATION_STAGES
    ]


def _recruitment_backlog(scope):
    open_apps = scope.filter(withdrawn_at__isnull=True, candidate__canceled=False)
    active_cases = ErasmusCase.all_objects.filter(
        application__in=open_apps, is_active=True
    )
    counts = {
        "awaiting_screening": open_apps.filter(
            Q(screening__isnull=True) | Q(screening__status__in=OPEN_SCREENING_STATUSES)
        ).count(),
        "verification_in_progress": open_apps.filter(
            verification_case__status__in=OPEN_VERIFICATION_STATUSES
        ).count(),
        "awaiting_interview_decision": InterviewBooking.all_objects.filter(
            invitation__application__in=open_apps,
            status=InterviewBooking.Status.COMPLETED,
            decision__isnull=True,
        )
        .order_by()
        .values("invitation__application_id")
        .distinct()
        .count(),
        "awaiting_date_confirmation": active_cases.filter(
            arrangement_status=ErasmusCase.ArrangementStatus.PROPOSED
        ).count(),
        "awaiting_letter_receipt": active_cases.filter(
            acceptance_status=ErasmusCase.AcceptanceStatus.SENT
        ).count(),
    }
    return [
        _point("recruitment_backlog_by_stage", counts[stage], {"stage": stage})
        for stage in RECRUITMENT_BACKLOG_STAGES
    ]


def _started_internships(onboardings):
    """Return onboarded internships with explicit native actual-start evidence."""

    # A planned onboarding date is not evidence that the internship actually
    # started. Require Horilla's native joining date so the planned and actual
    # start populations remain explicitly reconcilable.
    return onboardings.filter(
        status=InternOnboarding.Status.ONBOARDED,
        employee__isnull=False,
        employee__employee_work_info__date_joining__isnull=False,
    ).annotate(actual_start=F("employee__employee_work_info__date_joining"))


def _internship(query, onboardings):
    cutoff = query.cutoff
    started = _started_internships(onboardings)
    active = started.filter(
        actual_start__lte=query.as_of_date, end_date__gte=query.as_of_date
    )
    planned = onboardings.filter(
        start_date__gte=query.period_start, start_date__lte=cutoff
    )
    points = [
        _point(
            "internships_started",
            started.filter(
                actual_start__gte=query.period_start, actual_start__lte=cutoff
            ).count(),
        ),
        _point("planned_starts", planned.count()),
        _point(
            "starts_awaiting_onboarding_completion",
            planned.exclude(status=InternOnboarding.Status.ONBOARDED).count(),
        ),
        _point("active_interns", active.count()),
    ]
    points += _grouped(
        active, "venture_id", "venture__name", "venture", "active_interns_by_venture"
    )
    points += _grouped(
        active,
        "department_id",
        "department__department",
        "department",
        "active_interns_by_department",
    )
    future = onboardings.filter(
        status__in=(
            InternOnboarding.Status.APPROVED,
            InternOnboarding.Status.IN_PROGRESS,
            InternOnboarding.Status.ONBOARDED,
        ),
        start_date__gt=query.as_of_date,
    )
    for horizon in HORIZON_DAYS:
        limit = query.as_of_date + timedelta(days=horizon)
        points += [
            _point(
                "upcoming_starts",
                future.filter(start_date__lte=limit).count(),
                {"horizon_days": str(horizon)},
            ),
            _point(
                "upcoming_completions",
                active.filter(end_date__lte=limit).count(),
                {"horizon_days": str(horizon)},
            ),
        ]

    employee_ids = onboardings.filter(employee__isnull=False).values("employee_id")
    weekly = WeeklyReport.all_objects.filter(
        employee_id__in=employee_ids,
        deadline_at__date__gte=query.period_start,
        deadline_at__date__lte=cutoff,
    )
    first_submission = WeeklyReportVersion.all_objects.filter(
        report=OuterRef("pk"), version_number=1
    ).values("submitted_at")[:1]
    submitted = weekly.exclude(status=ReportStatus.DRAFT).annotate(
        first_submitted_at=Coalesce(Subquery(first_submission), "submitted_at")
    )
    monthly = MonthlyPresentation.all_objects.filter(
        employee_id__in=employee_ids,
        deadline_at__date__gte=query.period_start,
        deadline_at__date__lte=cutoff,
    )
    points += [
        _point("weekly_reports_due", weekly.count()),
        _point("weekly_reports_submitted", submitted.count()),
        _point(
            "weekly_reports_on_time",
            submitted.filter(first_submitted_at__lte=F("deadline_at")).count(),
        ),
        _point("monthly_presentations_due", monthly.count()),
        _point(
            "monthly_presentations_completed",
            monthly.filter(status=ReportStatus.APPROVED).count(),
        ),
    ]

    training = TrainingAssignment.all_objects.filter(
        employee_id__in=employee_ids,
        due_date__gte=query.period_start,
        due_date__lte=cutoff,
    ).exclude(status=TrainingAssignment.Status.CANCELLED)
    training_done = training.filter(status=TrainingAssignment.Status.COMPLETED).filter(
        Q(completed_at__isnull=True) | Q(completed_at__date__lte=query.as_of_date)
    )
    points.append(
        _rate_point("training_completion_rate", training_done.count(), training.count())
    )

    assignments = AssetAssignment._base_manager.filter(
        assigned_to_employee_id__in=employee_ids
    )
    points += [
        _point(
            "assets_allocated",
            assignments.filter(
                assigned_date__gte=query.period_start,
                assigned_date__lte=query.period_end,
            ).count(),
        ),
        _point(
            "assets_returned",
            assignments.filter(
                return_date__gte=query.period_start, return_date__lte=query.period_end
            ).count(),
        ),
        _point(
            "assets_outstanding",
            assignments.filter(assigned_date__lte=query.as_of_date)
            .filter(Q(return_date__isnull=True) | Q(return_date__gt=query.as_of_date))
            .count(),
        ),
    ]

    ending = onboardings.filter(
        status=InternOnboarding.Status.ONBOARDED,
        end_date__gte=query.period_start,
        end_date__lte=cutoff,
    )
    completed = ending.filter(
        completion_case__state=CompletionCase.State.COMPLETED,
        completion_case__completed_at__isnull=False,
        completion_case__completed_at__date__lte=query.as_of_date,
    )
    points.append(
        _rate_point("offboarding_completion_rate", completed.count(), ending.count())
    )
    return points


def _document_is_complete(status, signer_roles):
    Status = ErasmusCaseDocument.Status
    if signer_roles:
        return status == Status.RETURNED
    return status in (Status.APPROVED, Status.SIGNED, Status.RETURNED)


def _administration(query, scope, onboardings):
    outstanding = OnboardingChecklistLink.all_objects.filter(
        onboarding__in=onboardings.exclude(status=InternOnboarding.Status.ONBOARDED),
        required=True,
    ).exclude(native_task__status="done")
    points = [_point("outstanding_onboarding_tasks", outstanding.count())]
    points += _by_value(
        outstanding, "code", "checklist_item", "outstanding_onboarding_tasks_by_item"
    )

    documents = ErasmusCaseDocument.all_objects.filter(
        case__application__in=scope, case__is_active=True, is_active=True
    )
    points += _by_value(documents, "status", "status", "documents_by_status")
    ages = defaultdict(list)
    for status, signer_roles, created_at in documents.order_by().values_list(
        "status", "required_signer_roles", "created_at"
    ):
        created_on = _local_date(created_at)
        if created_on is None or created_on > query.as_of_date:
            continue
        if not _document_is_complete(status, signer_roles):
            ages[status].append((query.as_of_date - created_on).days)
    for status in sorted(ages):
        total = sum(ages[status])
        points.append(
            _point(
                "open_document_average_age_days",
                _round2(Decimal(total) / len(ages[status])),
                {"status": status},
                numerator=total,
                denominator=len(ages[status]),
            )
        )

    delivery_failures = (
        DeliveryAttempt.all_objects.filter(
            event__company_id=query.company_id,
            outcome=DeliveryAttempt.Outcome.FAILED,
            completed_at__isnull=False,
            **_in_period("completed_at", query),
        )
        .order_by()
        .values("event_id")
        .distinct()
        .count()
    )
    execution_failures = WorkflowExecution.all_objects.filter(
        event__company_id=query.company_id,
        status=WorkflowExecution.Status.FAILED,
        completed_at__isnull=False,
        **_in_period("completed_at", query),
    ).count()
    points += [
        _point("workflow_failures", delivery_failures, {"stage": "delivery"}),
        _point("workflow_failures", execution_failures, {"stage": "execution"}),
        _point(
            "workflow_dead_letter_backlog",
            LifecycleEvent.all_objects.filter(
                company_id=query.company_id, status=LifecycleEvent.Status.FAILED
            ).count(),
        ),
    ]
    points += _by_value(
        AccountProvisioningRequest.all_objects.filter(onboarding__in=onboardings),
        "status",
        "status",
        "account_provisioning_by_status",
    )
    points += _by_value(
        AccessRevocationTask.all_objects.filter(case__onboarding__in=onboardings),
        "status",
        "status",
        "access_revocation_by_status",
    )
    return points


def collect_metrics(query: AnalyticsQuery) -> list[dict]:
    scope = application_scope(query)
    onboardings = onboarding_scope(query)
    return [
        *_recruitment_volume(query, scope),
        *_screening_and_verification(query, scope),
        *_interviews_and_decisions(query, scope),
        *_offers_and_acceptance(query, scope),
        *_stage_durations(query, scope),
        *_recruitment_backlog(scope),
        *_internship(query, onboardings),
        *_administration(query, scope, onboardings),
    ]
