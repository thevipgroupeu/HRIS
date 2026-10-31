"""Versioned KPI and lifecycle-event vocabulary for PeopleOps analytics.

Definitions live in code so dashboard labels, validation, the Metabase reporting
contract and export cannot drift independently. The business-readable
specification in ``docs/analytics/PEOPLEOPS_KPIS.md`` is kept in sync with this
registry by an automated test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA_VERSION = 2

DASHBOARDS = ("executive", "recruitment", "internship", "administration")

# Counting units. Data-quality rule: a person, an application, an interview, an
# offer (Erasmus case with an acceptance letter) and an internship are never
# interchangeable, so every metric declares exactly one unit.
COUNT_UNITS = frozenset(
    {
        "person",
        "application",
        "interview",
        "case",
        "internship",
        "report",
        "presentation",
        "training",
        "asset",
        "task",
        "document",
        "request",
        "event",
    }
)
RATE_UNIT = "percent"
DURATION_UNIT = "days"

# Units whose small counts can single out an individual applicant or intern.
# Automation failures ("event") describe systems, not people.
PERSON_LINKED_UNITS = COUNT_UNITS - {"event"}

# period: event timestamp inside [period_start, period_end].
# cohort: records that entered in the period, outcome measured at as_of_date.
# as_of: state on as_of_date derived from immutable dates.
# current_state: backlog read at generation time (see snapshot.generated_at).
SEMANTICS = frozenset({"period", "cohort", "as_of", "current_state"})

FILTER_KEYS = frozenset(
    {"venture_id", "department_id", "role_id", "source", "period_start", "period_end"}
)

HORIZON_DAYS = (7, 30, 60, 90)

RECRUITMENT_DURATION_STAGES = (
    "application_to_first_screening",
    "verification_request_to_completion",
    "invitation_to_booking",
    "interview_to_decision",
    "selection_to_date_confirmation",
    "letter_sent_to_receipt",
)

RECRUITMENT_BACKLOG_STAGES = (
    "awaiting_screening",
    "verification_in_progress",
    "awaiting_interview_decision",
    "awaiting_date_confirmation",
    "awaiting_letter_receipt",
)

WORKFLOW_FAILURE_STAGES = ("delivery", "execution")

# Fail closed when a dimension, filter or reporting column would expose
# identifiers, documents, medical data or protected characteristics. Tokens are
# matched against ``_``-separated words, never substrings, so approved metric
# names such as ``documents_by_status`` are not affected.
PROHIBITED_EXPORT_TOKENS = frozenset(
    {
        "name",
        "email",
        "phone",
        "mobile",
        "address",
        "birth",
        "dob",
        "age",
        "nationality",
        "citizenship",
        "country",
        "gender",
        "sex",
        "race",
        "ethnicity",
        "ethnic",
        "religion",
        "disability",
        "medical",
        "health",
        "marital",
        "salary",
        "payroll",
        "iban",
        "identity",
        "passport",
        "file",
        "candidate",
        "person",
        "employee",
        "user",
    }
)

# Protected characteristics that must never feed analytics queries at all.
PROTECTED_CHARACTERISTIC_TOKENS = frozenset(
    {
        "nationality",
        "citizenship",
        "country",
        "gender",
        "sex",
        "dob",
        "birth",
        "race",
        "ethnic",
        "ethnicity",
        "religion",
        "disability",
        "medical",
        "health",
        "marital",
        "passport",
    }
)

_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+")
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def tokens(identifier: str) -> set[str]:
    """Split an identifier such as ``filter_venture_id`` into lowercase words."""

    return {part for part in re.split(r"[^a-z0-9]+", identifier.lower()) if part}


def prohibited_tokens(identifier: str) -> set[str]:
    return tokens(identifier) & PROHIBITED_EXPORT_TOKENS


def looks_like_identifier(value) -> bool:
    """True when a dimension value looks like an e-mail address or UUID."""

    text = str(value)
    return bool(_EMAIL_PATTERN.search(text) or _UUID_PATTERN.search(text))


@dataclass(frozen=True)
class EventMapping:
    """An analytics event derived from an authoritative, timestamped record.

    ``model`` and ``timestamp`` are checked against the live model registry by
    tests, so a renamed field cannot silently break a KPI.
    """

    key: str
    model: str
    timestamp: str
    condition: str = ""
    immutable: bool = True


EVENT_MAPPINGS = {
    mapping.key: mapping
    for mapping in (
        EventMapping(
            "application.submitted", "tvg_careers.Application", "submitted_at"
        ),
        EventMapping(
            "application.withdrawn",
            "tvg_careers.Application",
            "withdrawn_at",
            immutable=False,
        ),
        EventMapping(
            "screening.decided",
            "tvg_erasmus.ScreeningEvent",
            "occurred_at",
            "to_status is the screening outcome",
        ),
        EventMapping(
            "verification.requested",
            "tvg_erasmus.VerificationCase",
            "requested_at",
            "status != not_required",
            immutable=False,
        ),
        EventMapping(
            "verification.completed",
            "tvg_erasmus.VerificationCase",
            "completed_at",
            "status = verified",
            immutable=False,
        ),
        EventMapping(
            "interview.invited", "tvg_interviews.InterviewInvitation", "invited_at"
        ),
        EventMapping(
            "interview.booked",
            "tvg_interviews.InterviewBooking",
            "booked_at",
            "status != cancelled",
        ),
        EventMapping(
            "interview.completed",
            "tvg_interviews.InterviewBooking",
            "completed_at",
            "status = completed",
        ),
        EventMapping(
            "interview.decided",
            "tvg_interviews.InterviewDecision",
            "decided_at",
            "latest decision per application as of the as-of date",
        ),
        EventMapping(
            "arrangements.dates_confirmed",
            "tvg_erasmus.ErasmusCaseEvent",
            "occurred_at",
            "event_type = erasmus.dates_confirmed; first event per case",
        ),
        EventMapping(
            "acceptance.issued",
            "tvg_erasmus.AcceptanceLetter",
            "issue_date",
            "first letter version per case",
        ),
        EventMapping(
            "acceptance.sent",
            "tvg_erasmus.AcceptanceLetter",
            "sent_at",
            "first letter version per case",
        ),
        EventMapping(
            "acceptance.received",
            "tvg_erasmus.AcceptanceLetter",
            "received_at",
            "first candidate receipt confirmation per case",
        ),
        EventMapping(
            "internship.planned_start", "tvg_onboarding.InternOnboarding", "start_date"
        ),
        EventMapping(
            "internship.started",
            "employee.EmployeeWorkInformation",
            "date_joining",
            "InternOnboarding.status = onboarded and date_joining is present",
            immutable=False,
        ),
        EventMapping(
            "internship.planned_end", "tvg_onboarding.InternOnboarding", "end_date"
        ),
        EventMapping("weekly_report.due", "tvg_reporting.WeeklyReport", "deadline_at"),
        EventMapping(
            "weekly_report.first_submitted",
            "tvg_reporting.WeeklyReportVersion",
            "submitted_at",
            "version_number = 1",
        ),
        EventMapping(
            "monthly_presentation.due",
            "tvg_reporting.MonthlyPresentation",
            "deadline_at",
        ),
        EventMapping(
            "training.due",
            "tvg_training.TrainingAssignment",
            "due_date",
            "status != cancelled",
        ),
        EventMapping(
            "asset.assigned",
            "asset.AssetAssignment",
            "assigned_date",
            "assigned to an intern employee",
        ),
        EventMapping(
            "asset.returned",
            "asset.AssetAssignment",
            "return_date",
            "assigned to an intern employee",
            immutable=False,
        ),
        EventMapping(
            "internship.completed",
            "tvg_offboarding.CompletionCase",
            "completed_at",
            "state = completed",
        ),
        EventMapping(
            "automation.delivery_failed",
            "tvg_automation.DeliveryAttempt",
            "completed_at",
            "outcome = failed",
        ),
        EventMapping(
            "automation.execution_failed",
            "tvg_automation.WorkflowExecution",
            "completed_at",
            "status = failed",
            immutable=False,
        ),
    )
}


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    dashboard: str
    unit: str
    semantics: str
    definition: str
    sources: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    company_wide: bool = False

    @property
    def is_breakdown(self) -> bool:
        return bool(self.dimensions)

    @property
    def is_rate(self) -> bool:
        return self.unit == RATE_UNIT

    @property
    def is_duration(self) -> bool:
        return self.unit == DURATION_UNIT

    @property
    def is_count(self) -> bool:
        return self.unit in COUNT_UNITS


def _m(*args, **kwargs):
    return MetricDefinition(*args, **kwargs)


_APPLICATION = ("application.submitted",)

METRICS = {
    definition.key: definition
    for definition in (
        # --- Recruitment volume -------------------------------------------------
        _m(
            "applications_total",
            "Applications submitted",
            "executive",
            "application",
            "period",
            "Application records submitted in the period. One person can submit "
            "several applications; withdrawn applications are included.",
            _APPLICATION,
        ),
        _m(
            "unique_persons",
            "Unique applicants",
            "executive",
            "person",
            "period",
            "Distinct permanent Person records behind the applications submitted "
            "in the period. Never greater than applications submitted.",
            _APPLICATION,
        ),
        _m(
            "applications_withdrawn",
            "Applications withdrawn",
            "recruitment",
            "application",
            "cohort",
            "Applications submitted in the period that were withdrawn on or "
            "before the as-of date.",
            ("application.submitted", "application.withdrawn"),
        ),
        _m(
            "applications_by_source",
            "Applications by recruitment source",
            "recruitment",
            "application",
            "period",
            "Applications submitted in the period grouped by the CareerHub "
            "attribution source.",
            _APPLICATION,
            ("source",),
        ),
        _m(
            "applications_by_venture",
            "Applications by venture",
            "recruitment",
            "application",
            "period",
            "Applications submitted in the period grouped by the opening venture.",
            _APPLICATION,
            ("venture", "venture_id"),
        ),
        _m(
            "applications_by_department",
            "Applications by department",
            "recruitment",
            "application",
            "period",
            "Applications submitted in the period grouped by the department of "
            "the opening's job position.",
            _APPLICATION,
            ("department", "department_id"),
        ),
        _m(
            "applications_by_role",
            "Applications by role",
            "recruitment",
            "application",
            "period",
            "Applications submitted in the period grouped by the opening's job "
            "position.",
            _APPLICATION,
            ("role", "role_id"),
        ),
        _m(
            "applications_by_month",
            "Applications by month",
            "recruitment",
            "application",
            "period",
            "Applications submitted in the period grouped by local calendar "
            "month (first day of the month).",
            _APPLICATION,
            ("month",),
        ),
        # --- Screening and verification ---------------------------------------
        _m(
            "applications_screened",
            "Applications screened",
            "recruitment",
            "application",
            "period",
            "Distinct applications with at least one human screening decision "
            "recorded in the period.",
            ("screening.decided",),
        ),
        _m(
            "initial_screening_outcomes",
            "Initial screening outcomes",
            "recruitment",
            "application",
            "period",
            "Distinct applications that reached each screening outcome in the "
            "period, from the immutable screening history. An application that "
            "moved from hold to shortlisted appears in both buckets.",
            ("screening.decided",),
            ("screening_outcome",),
        ),
        _m(
            "verification_requested",
            "Verifications requested",
            "recruitment",
            "application",
            "cohort",
            "Verification cases requested in the period, excluding cases where "
            "verification was not required.",
            ("verification.requested",),
        ),
        _m(
            "verification_completion_rate",
            "Verification completion rate",
            "recruitment",
            "percent",
            "cohort",
            "Of the verifications requested in the period, the share verified on "
            "or before the as-of date.",
            ("verification.requested", "verification.completed"),
        ),
        # --- Interviews and decisions ------------------------------------------
        _m(
            "interviews_total",
            "Interviews booked",
            "executive",
            "interview",
            "period",
            "Interview bookings made in the period that were not cancelled. One "
            "application can have several interviews.",
            ("interview.booked",),
        ),
        _m(
            "interviews_completed",
            "Interviews held",
            "recruitment",
            "interview",
            "period",
            "Interview bookings marked completed in the period.",
            ("interview.completed",),
        ),
        _m(
            "applications_interviewed",
            "Applications interviewed",
            "recruitment",
            "application",
            "period",
            "Distinct applications with at least one interview completed in the "
            "period.",
            ("interview.completed",),
        ),
        _m(
            "selected_candidates",
            "Selected (interview decision)",
            "recruitment",
            "application",
            "period",
            "Applications whose latest human interview decision, as of the as-of "
            "date, is 'selected' and was recorded in the period.",
            ("interview.decided",),
        ),
        _m(
            "rejected_candidates",
            "Rejected (interview decision)",
            "recruitment",
            "application",
            "period",
            "Applications whose latest human interview decision, as of the as-of "
            "date, is 'rejected' and was recorded in the period. Screening "
            "rejections are reported under initial screening outcomes.",
            ("interview.decided",),
        ),
        _m(
            "on_hold_candidates",
            "On hold (interview decision)",
            "recruitment",
            "application",
            "period",
            "Applications whose latest human interview decision, as of the as-of "
            "date, is 'hold' and was recorded in the period.",
            ("interview.decided",),
        ),
        # --- Arrangements, offers and acceptance -------------------------------
        _m(
            "date_confirmations",
            "Internship dates confirmed",
            "recruitment",
            "case",
            "period",
            "Erasmus cases whose candidate first confirmed the proposed internship "
            "dates in the period (immutable case history).",
            ("arrangements.dates_confirmed",),
        ),
        _m(
            "offers_issued",
            "Offers issued (acceptance letters)",
            "executive",
            "case",
            "period",
            "Erasmus cases whose first acceptance letter was issued in the period. "
            "Re-issued letter versions are not counted again.",
            ("acceptance.issued",),
        ),
        _m(
            "accepted_candidates",
            "Accepted (letter receipt confirmed)",
            "executive",
            "case",
            "period",
            "Erasmus cases whose candidate first confirmed receipt of the sent "
            "acceptance letter in the period.",
            ("acceptance.received",),
        ),
        _m(
            "internships_started",
            "Internships actually started",
            "executive",
            "internship",
            "period",
            "Onboarded intern records whose Employee joining date is present, "
            "falls in the period, and is not after the as-of date. Planned "
            "start dates are never substituted for actual starts.",
            ("internship.started",),
        ),
        # --- Recruitment bottlenecks -------------------------------------------
        _m(
            "stage_average_duration_days",
            "Average stage duration",
            "recruitment",
            "days",
            "period",
            "Mean elapsed days between two recruitment milestones for transitions "
            "completed in the period. Numerator is the total days and denominator "
            "the number of transitions.",
            (
                "application.submitted",
                "screening.decided",
                "verification.completed",
                "interview.invited",
                "interview.booked",
                "interview.completed",
                "interview.decided",
                "arrangements.dates_confirmed",
                "acceptance.sent",
                "acceptance.received",
            ),
            ("stage",),
        ),
        _m(
            "recruitment_backlog_by_stage",
            "Open recruitment items by stage",
            "recruitment",
            "application",
            "current_state",
            "Applications currently waiting at each recruitment stage. Read at "
            "snapshot generation time; ignores the reporting period.",
            (),
            ("stage",),
        ),
        # --- Internship ----------------------------------------------------------
        _m(
            "active_interns",
            "Active interns",
            "executive",
            "internship",
            "as_of",
            "Onboarded interns whose start is on or before and whose planned end "
            "is on or after the as-of date.",
            ("internship.started", "internship.planned_end"),
        ),
        _m(
            "active_interns_by_venture",
            "Active interns by venture",
            "internship",
            "internship",
            "as_of",
            "Active interns grouped by the venture recorded on the onboarding.",
            ("internship.started", "internship.planned_end"),
            ("venture", "venture_id"),
        ),
        _m(
            "active_interns_by_department",
            "Active interns by department",
            "internship",
            "internship",
            "as_of",
            "Active interns grouped by the department recorded on the onboarding.",
            ("internship.started", "internship.planned_end"),
            ("department", "department_id"),
        ),
        _m(
            "planned_starts",
            "Planned starts",
            "internship",
            "internship",
            "period",
            "Intern onboardings whose planned start date falls in the period and "
            "is not after the as-of date.",
            ("internship.planned_start",),
        ),
        _m(
            "starts_awaiting_onboarding_completion",
            "Starts awaiting onboarding completion",
            "internship",
            "internship",
            "period",
            "Planned starts in the period whose onboarding is still in progress, "
            "explaining the gap between planned and actual starts.",
            ("internship.planned_start",),
        ),
        _m(
            "upcoming_starts",
            "Starting within N days",
            "internship",
            "internship",
            "as_of",
            "Approved onboardings starting after the as-of date and within the "
            "horizon. Horizons are cumulative: filter on one horizon.",
            ("internship.planned_start",),
            ("horizon_days",),
        ),
        _m(
            "upcoming_completions",
            "Completing within N days",
            "internship",
            "internship",
            "as_of",
            "Active interns whose planned end date is within the horizon after the "
            "as-of date. Horizons are cumulative: filter on one horizon.",
            ("internship.planned_end",),
            ("horizon_days",),
        ),
        _m(
            "weekly_reports_due",
            "Weekly reports due",
            "internship",
            "report",
            "period",
            "Weekly report records of interns whose deadline falls in the period "
            "and not after the as-of date. Weeks for which an intern never opened "
            "a report are not counted.",
            ("weekly_report.due",),
        ),
        _m(
            "weekly_reports_submitted",
            "Weekly reports submitted",
            "internship",
            "report",
            "period",
            "Weekly reports due in the period that are no longer drafts.",
            ("weekly_report.due", "weekly_report.first_submitted"),
        ),
        _m(
            "weekly_reports_on_time",
            "Weekly reports on time",
            "internship",
            "report",
            "period",
            "Weekly reports due in the period whose first submission was no later "
            "than the deadline. Later resubmissions do not make a report late.",
            ("weekly_report.due", "weekly_report.first_submitted"),
        ),
        _m(
            "monthly_presentations_due",
            "Monthly presentations due",
            "internship",
            "presentation",
            "period",
            "Monthly presentation records of interns whose deadline falls in the "
            "period and not after the as-of date.",
            ("monthly_presentation.due",),
        ),
        _m(
            "monthly_presentations_completed",
            "Monthly presentations approved",
            "internship",
            "presentation",
            "period",
            "Monthly presentations due in the period whose current status is "
            "approved.",
            ("monthly_presentation.due",),
        ),
        _m(
            "training_completion_rate",
            "Training completion rate",
            "internship",
            "percent",
            "cohort",
            "Completed / non-cancelled training assignments of interns due in the "
            "period and not after the as-of date.",
            ("training.due",),
        ),
        _m(
            "assets_allocated",
            "Assets allocated",
            "internship",
            "asset",
            "period",
            "Asset assignments to intern employees made in the period, including "
            "assignments made in the PeopleOps asset screens.",
            ("asset.assigned",),
        ),
        _m(
            "assets_returned",
            "Assets returned",
            "internship",
            "asset",
            "period",
            "Asset assignments of intern employees returned in the period.",
            ("asset.returned",),
        ),
        _m(
            "assets_outstanding",
            "Assets not yet returned",
            "internship",
            "asset",
            "as_of",
            "Asset assignments of intern employees made on or before the as-of "
            "date and not returned by then.",
            ("asset.assigned", "asset.returned"),
        ),
        _m(
            "offboarding_completion_rate",
            "Offboarding completion rate",
            "internship",
            "percent",
            "cohort",
            "Of onboarded interns whose planned end date falls in the period and "
            "is not after the as-of date, the share with a completed completion "
            "case on or before the as-of date.",
            ("internship.planned_end", "internship.completed"),
        ),
        # --- Administration --------------------------------------------------------
        _m(
            "outstanding_onboarding_tasks",
            "Outstanding onboarding tasks",
            "administration",
            "task",
            "current_state",
            "Required onboarding checklist tasks not done, for onboardings that "
            "are not yet completed. Read at generation time.",
        ),
        _m(
            "outstanding_onboarding_tasks_by_item",
            "Outstanding onboarding tasks by checklist item",
            "administration",
            "task",
            "current_state",
            "Outstanding onboarding tasks grouped by checklist item code.",
            (),
            ("checklist_item",),
        ),
        _m(
            "documents_by_status",
            "Erasmus documents by status",
            "administration",
            "document",
            "current_state",
            "Erasmus case documents of active cases grouped by processing status. "
            "Read at generation time.",
            (),
            ("status",),
        ),
        _m(
            "open_document_average_age_days",
            "Average age of open documents",
            "administration",
            "days",
            "current_state",
            "Mean days since creation of documents that are not yet complete, by "
            "status. Numerator is total days, denominator the document count.",
            (),
            ("status",),
        ),
        _m(
            "workflow_failures",
            "Workflow failures",
            "administration",
            "event",
            "period",
            "Lifecycle events with a failed delivery attempt in the period "
            "(delivery) and failed external workflow executions completed in the "
            "period (execution). Company-wide; other filters do not apply.",
            ("automation.delivery_failed", "automation.execution_failed"),
            ("stage",),
            True,
        ),
        _m(
            "workflow_dead_letter_backlog",
            "Workflow events needing review",
            "administration",
            "event",
            "current_state",
            "Lifecycle events currently in the failed (dead-letter) state. "
            "Company-wide; other filters do not apply.",
            (),
            (),
            True,
        ),
        _m(
            "account_provisioning_by_status",
            "Account provisioning by status",
            "administration",
            "request",
            "current_state",
            "Intern account provisioning requests grouped by current status.",
            (),
            ("status",),
        ),
        _m(
            "access_revocation_by_status",
            "Access revocation by status",
            "administration",
            "request",
            "current_state",
            "Internship completion access-revocation tasks grouped by current "
            "status.",
            (),
            ("status",),
        ),
    )
}

METRIC_KEYS = frozenset(METRICS)
SCALAR_METRIC_KEYS = frozenset(k for k, m in METRICS.items() if not m.is_breakdown)
DIMENSION_KEYS = frozenset(d for m in METRICS.values() for d in m.dimensions)
LABELS = {key: metric.label for key, metric in METRICS.items()}
KPI_DEFINITIONS = {key: metric.definition for key, metric in METRICS.items()}

# Headline cards per dashboard, in display order.
CARD_KEYS = {
    "executive": (
        "unique_persons",
        "applications_total",
        "interviews_total",
        "offers_issued",
        "accepted_candidates",
        "internships_started",
        "active_interns",
    ),
    "recruitment": (
        "applications_total",
        "unique_persons",
        "applications_screened",
        "verification_completion_rate",
        "interviews_total",
        "applications_interviewed",
        "selected_candidates",
        "rejected_candidates",
        "date_confirmations",
        "offers_issued",
        "accepted_candidates",
        "internships_started",
    ),
}

# Secondary scalar metrics shown under the cards on each dashboard.
DETAIL_KEYS = {
    "executive": (
        "planned_starts",
        "starts_awaiting_onboarding_completion",
        "weekly_reports_due",
        "weekly_reports_submitted",
        "weekly_reports_on_time",
        "monthly_presentations_due",
        "monthly_presentations_completed",
        "training_completion_rate",
        "assets_allocated",
        "assets_returned",
        "assets_outstanding",
        "offboarding_completion_rate",
        "outstanding_onboarding_tasks",
        "workflow_dead_letter_backlog",
    ),
    "recruitment": (
        "applications_withdrawn",
        "verification_requested",
        "interviews_completed",
        "on_hold_candidates",
    ),
}

BREAKDOWN_KEYS = {
    "executive": (
        "upcoming_starts",
        "upcoming_completions",
        "active_interns_by_venture",
        "active_interns_by_department",
        "outstanding_onboarding_tasks_by_item",
        "documents_by_status",
        "open_document_average_age_days",
        "workflow_failures",
        "account_provisioning_by_status",
        "access_revocation_by_status",
    ),
    "recruitment": (
        "applications_by_source",
        "applications_by_venture",
        "applications_by_department",
        "applications_by_role",
        "applications_by_month",
        "initial_screening_outcomes",
        "stage_average_duration_days",
        "recruitment_backlog_by_stage",
    ),
}
