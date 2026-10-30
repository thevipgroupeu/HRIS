# PeopleOps KPI specification

**KPI schema version**: 2 (`tvg_analytics.definitions.SCHEMA_VERSION`)  
**Owner**: THEVIPGROUP PeopleOps, with engineering for the code registry  
**Related**: ADR-016, `docs/integrations/METABASE_REPORTING.md`

This document gives the business meaning of every PeopleOps KPI produced by the
isolated `tvg_analytics` app. `tvg_analytics/definitions.py` is authoritative:
it holds the metric registry (`METRICS`), the event vocabulary
(`EVENT_MAPPINGS`), the allowed filters and dimensions and the privacy tokens.
`tvg_analytics/tests/test_docs.py` fails when a registered metric or event is
missing from this document, when a metric table names an unregistered key, or
when a documented label, unit or semantics differs from the registry.

## Purpose and scope

The KPIs answer operational questions for one legal employer (one Horilla
`Company`): how many people applied and through which source, where recruitment
work is waiting, how many interviews, offers, acceptances and internship starts
occurred, how interns are progressing, and which administrative or workflow
items are open.

They are shown in three places:

1. the PeopleOps dashboards at `/peopleops/analytics/` (executive) and
   `/peopleops/analytics/recruitment/`, which compute live values for the
   selected filters;
2. two Horilla standard reports registered by `tvg_analytics` (see
   [Horilla standard reports](#horilla-standard-reports));
3. Metabase, which reads only published (exported) snapshots through the
   curated reporting view `tvg_analytics_reporting.metric_point_v1`.

Out of scope: person-level reporting, performance evaluation, ranking and any
hiring, rejection, verification or offer decision. Horilla's native reports are
not changed.

The KPI schema version identifies the meaning of the metric set. A change to a
definition, unit or dimension increments `SCHEMA_VERSION`; the version is part
of the snapshot fingerprint, so a snapshot of an older version is never reused
for a newer one. Phase 17 starts with schema version 2 because version 1 was a
pre-release prototype and was never retained as a deployable analytics schema.

## Counting units and data-quality rules

Every metric declares exactly one unit. Counts use one of `person`,
`application`, `interview`, `case`, `internship`, `report`, `presentation`,
`training`, `asset`, `task`, `document`, `request` or `event`; rates use
`percent` and averages use `days`. A person, an application, an interview, an
offer and an internship are never interchangeable: never add, subtract or
divide values with different units, and never derive a conversion rate from two
period counts.

### Unique persons

A unique person is a distinct `tvg_core.Person` behind the applications
submitted in the period. Person is the employer-scoped stable identity
(ADR-001); its e-mail is unique per legal employer, case-insensitively, and no
automatic e-mail merge takes place. One person can apply to several openings,
so `unique_persons` is never greater than `applications_total`, and the
snapshot validation checks this.

### Applications

An application is one `tvg_careers.Application`: the link between one Person,
one CareerHub `JobOpening` and one native Horilla `Candidate`. A person can
apply once per opening. Applications are counted by `submitted_at`, which is
immutable. Withdrawn applications stay in `applications_total`; withdrawal is
reported separately. An application belongs to the company only when both the
opening's venture (`Venture.legal_employer`) and the opening's Horilla
`Recruitment.company` are that company. The snapshot also counts applications
without the recruitment-company join and fails validation when the two counts
differ.

### Interviews

An interview is one `tvg_interviews.InterviewBooking`, not an application. One
application can have several bookings. `interviews_total` counts bookings made
in the period (`booked_at`) whose status is not `cancelled` when the snapshot is
built; `interviews_completed` counts bookings completed in the period;
`applications_interviewed` counts distinct applications with a completed
interview. Interview decisions are counted per application from the latest
human `InterviewDecision` on or before the as-of date.

### Offers (Erasmus cases with a first acceptance letter)

An offer is an `tvg_erasmus.ErasmusCase` whose first `AcceptanceLetter` was
issued in the period. "First" means the earliest `issue_date` over all letter
versions of the case; a re-issued version (ADR-008 never overwrites a letter)
is not counted again. An interview `selected` decision is not an offer: under
ADR-008 a selected case still needs candidate date confirmation, university and
funding confirmation, HR approval and an explicit human issue action before a
letter exists. Horilla's native offer-letter fields are not used.

### Accepted candidates

An accepted candidate is an Erasmus case whose candidate first confirmed
receipt of a sent acceptance letter in the period (earliest
`AcceptanceLetter.received_at` per case). Under ADR-008, "send" makes the
latest issued letter available in the candidate's CareerHub account and the
candidate then explicitly records receipt; that received state is the formal
acceptance step and is the only state that exposes Learning Agreement
readiness.

Date confirmation is a separate KPI (`date_confirmations`). The candidate
confirms the proposed internship dates before HR approval and before any
acceptance letter exists. It records agreement on dates, not acceptance of an
issued offer, and a case can confirm dates and still never be approved or
issued a letter. Counting it as acceptance would overstate acceptances and
hide cases that stall between date confirmation and issue.

### Internships actually started

An internship has actually started when its `tvg_onboarding.InternOnboarding`
has status `onboarded`, is linked to a Horilla `Employee`, and the start date is
in the period and not after the as-of date. The start date is the Employee's
`EmployeeWorkInformation.date_joining`. A missing joining date is not evidence
of an actual start and is never replaced with the onboarding's planned date.
Planned starts are counted separately
(`planned_starts`, any onboarding status), and
`starts_awaiting_onboarding_completion` explains the difference. The joining
date is editable in Horilla, so this event is marked mutable and a rebuilt
snapshot can differ.

### Why the numbers are never one decreasing funnel

Period KPIs count events whose own timestamp falls in the period. A person who
applies in March, is interviewed in April, receives a letter in May and starts
in June is counted in a different period at every stage. The stages also count
different units: one application can have several interviews, so
`interviews_total` can exceed `applications_total` for the same period, and
date confirmations, offers and acceptances count cases. For any single period,
therefore, "applications ≥ interviews ≥ offers ≥ accepted ≥ started" does not
hold, and the dashboards do not present those numbers as a funnel or as
stage-to-stage conversion rates. Validation checks only subset relations that
hold within one population (see
[Automated validation and reconciliation](#automated-validation-and-reconciliation)).
Conversion analysis needs cohort KPIs; adding one requires a new KPI schema
version.

## Period semantics and as-of rules

| Semantics | Meaning |
|---|---|
| period | Counts events whose local date is between the period start and the period end inclusive, and not after the as-of date. |
| cohort | Takes the records that entered in the period (for example verifications requested) and counts the outcome only when it happened on or before the as-of date. |
| as_of | Reports the state on the as-of date, derived from dates on the records (start, planned end, assignment and return dates). |
| current_state | Reports the backlog when the snapshot is generated (`generated_at`); the period and as-of date do not restrict it. |

As-of rules:

1. Dates are local calendar dates in the configured `TIME_ZONE`
   (`Europe/Warsaw`). Timestamps are converted to the local date before they are
   compared with the period.
2. The PeopleOps dashboards and the Horilla reports use the period end as the
   as-of date. The dashboard filter form rejects a period that ends in the
   future or is longer than 366 days; the default is the last 30 days. A
   snapshot can never have an as-of date before its period end.
3. A cohort outcome recorded after the as-of date is not counted, even if the
   snapshot is generated later.
4. `selected_candidates`, `rejected_candidates` and `on_hold_candidates` use the
   latest decision per application recorded on or before the as-of date, and
   count it when that decision was recorded in the period. A decision reversed
   after the as-of date does not change these counts.
5. Some period and cohort metrics read a status that can change later: booking
   cancellation (`interviews_total`), weekly report draft status, monthly
   presentation approval, verification status, training status and the
   intern's joining date. The event table marks such sources as mutable. A
   snapshot freezes the values at generation time; rebuild it to include late
   changes.
6. `open_document_average_age_days` is a current-state metric whose age is
   measured to the as-of date; documents created after the as-of date are
   ignored.
7. To reproduce a number later, read the snapshot. Re-running the same filters
   against live data can give a different result.

## Filters

The approved filter keys are `venture_id`, `department_id`, `role_id`,
`source`, `period_start` and `period_end`. Any other filter key fails snapshot
validation. The company is never a filter: the PeopleOps dashboards always use
the requesting employee's own company and refuse a different selected Horilla
company, and the Horilla reports use Horilla's selected company only.

| Filter | Recruitment metrics | Internship metrics | Administration metrics |
|---|---|---|---|
| Venture | The opening's venture (`JobOpening.venture`) | The venture recorded on the `InternOnboarding` | Erasmus documents follow the recruitment rule; onboarding tasks, provisioning and access revocation follow the internship rule |
| Department | The department of the job position of the opening's Horilla `Recruitment` | The department recorded on the `InternOnboarding` | As for venture |
| Role | The job position of the opening's Horilla `Recruitment` | The job position of the originating application's opening (through the Erasmus case) | As for venture |
| Source | `Application.source` | The originating application's source | As for venture |
| Period | As defined by each metric's semantics | As defined by each metric's semantics | As defined by each metric's semantics |

- Source values are the CareerHub attribution allow-list in
  `tvg_careers.attribution.APPLICATION_SOURCES`: `company_website`, `linkedin`,
  `erasmusintern`, `job_board`, `university_referral` and `referral`. They are
  not Horilla `Candidate.source` values.
- When a role or source filter is set, onboardings without an originating
  Erasmus case and application are excluded.
- Workflow metrics (`workflow_failures`, `workflow_dead_letter_backlog`) are
  company-wide; the venture, department, role and source filters do not apply
  to them. Their definitions say so, and the dashboard labels the workflow
  failures breakdown "company-wide".
- Breakdown rows whose venture, department or role is missing use the label
  `(unassigned)`.

## KPI catalogue

The `dashboard` value below is the metric's home dashboard: it is stored on
every snapshot point and in the `dashboard` column of the reporting view. The
PeopleOps executive page also shows the internship and administration metrics;
the recruitment page shows the recruitment metrics and the executive headline
counts.

Dimension values: `venture`, `department` and `role` are names with matching
numeric `_id` dimensions; `month` is the first day of a local month
(`YYYY-MM-01`); `horizon_days` is `7`, `30`, `60` or `90`; `stage`,
`status`, `screening_outcome` and `checklist_item` are internal codes from
allow-listed domains.

### Executive dashboard

| Metric key | Label | Unit | Semantics | Dimensions | Definition | Source events |
|---|---|---|---|---|---|---|
| `applications_total` | Applications submitted | application | period | none | CareerHub applications submitted in the period. One person can submit several applications; withdrawn applications stay counted. | `application.submitted` |
| `unique_persons` | Unique applicants | person | period | none | Distinct permanent Person records behind the applications submitted in the period. Never greater than applications submitted. | `application.submitted` |
| `interviews_total` | Interviews booked | interview | period | none | Interview bookings made in the period that are not cancelled when the snapshot is built. One application can have several interviews. | `interview.booked` |
| `offers_issued` | Offers issued (acceptance letters) | case | period | none | Erasmus cases whose first acceptance letter was issued in the period. Re-issued letter versions are not counted again. | `acceptance.issued` |
| `accepted_candidates` | Accepted (letter receipt confirmed) | case | period | none | Erasmus cases whose candidate first confirmed receipt of the sent acceptance letter in the period. | `acceptance.received` |
| `internships_started` | Internships actually started | internship | period | none | Onboarded intern records linked to an Employee whose joining date is present, falls in the period and is not after the as-of date. Planned dates are never substituted. | `internship.started` |
| `active_interns` | Active interns | internship | as_of | none | Onboarded interns linked to an Employee whose actual start is on or before, and whose planned end is on or after, the as-of date. | `internship.started`, `internship.planned_end` |

### Recruitment dashboard

| Metric key | Label | Unit | Semantics | Dimensions | Definition | Source events |
|---|---|---|---|---|---|---|
| `applications_withdrawn` | Applications withdrawn | application | cohort | none | Applications submitted in the period that were withdrawn on or before the as-of date. | `application.submitted`, `application.withdrawn` |
| `applications_by_source` | Applications by recruitment source | application | period | `source` | Applications submitted in the period grouped by CareerHub attribution source. Rows add up to applications submitted. | `application.submitted` |
| `applications_by_venture` | Applications by venture | application | period | `venture`, `venture_id` | Applications submitted in the period grouped by the opening's venture. Rows add up to applications submitted. | `application.submitted` |
| `applications_by_department` | Applications by department | application | period | `department`, `department_id` | Applications submitted in the period grouped by the department of the opening's job position. Rows add up to applications submitted. | `application.submitted` |
| `applications_by_role` | Applications by role | application | period | `role`, `role_id` | Applications submitted in the period grouped by the opening's job position. Rows add up to applications submitted. | `application.submitted` |
| `applications_by_month` | Applications by month | application | period | `month` | Applications submitted in the period grouped by local calendar month. Rows add up to applications submitted. | `application.submitted` |
| `applications_screened` | Applications screened | application | period | none | Distinct applications with at least one human screening decision (any outcome other than initial screening) recorded in the period. | `screening.decided` |
| `initial_screening_outcomes` | Initial screening outcomes | application | period | `screening_outcome` | Distinct applications that reached each screening outcome in the period, from the immutable screening history. An application that moved from hold to shortlisted appears in both rows, so rows do not add up to applications screened. | `screening.decided` |
| `verification_requested` | Verifications requested | application | cohort | none | Verification cases requested in the period, excluding cases where verification is not required. | `verification.requested` |
| `verification_completion_rate` | Verification completion rate | percent | cohort | none | Of the verifications requested in the period, the share with status verified and completed on or before the as-of date. | `verification.requested`, `verification.completed` |
| `interviews_completed` | Interviews held | interview | period | none | Interview bookings with status completed whose completion was recorded in the period. | `interview.completed` |
| `applications_interviewed` | Applications interviewed | application | period | none | Distinct applications with at least one interview completed in the period. | `interview.completed` |
| `selected_candidates` | Selected (interview decision) | application | period | none | Applications whose latest human interview decision on or before the as-of date is selected and was recorded in the period. | `interview.decided` |
| `rejected_candidates` | Rejected (interview decision) | application | period | none | Applications whose latest human interview decision on or before the as-of date is rejected and was recorded in the period. Screening rejections are reported under initial screening outcomes. | `interview.decided` |
| `on_hold_candidates` | On hold (interview decision) | application | period | none | Applications whose latest human interview decision on or before the as-of date is hold and was recorded in the period. | `interview.decided` |
| `date_confirmations` | Internship dates confirmed | case | period | none | Erasmus cases whose candidate first confirmed the proposed internship dates in the period, from the immutable case history. Not an acceptance. | `arrangements.dates_confirmed` |
| `stage_average_duration_days` | Average stage duration | days | period | `stage` | Mean elapsed days for each recruitment transition completed in the period: application to first screening decision, verification request to completion, invitation to booking, completed interview to decision, selection decision to date confirmation, and letter sent to receipt (per letter version). Numerator is total days, denominator the number of transitions; negative intervals are ignored. | `application.submitted`, `screening.decided`, `verification.completed`, `interview.invited`, `interview.booked`, `interview.completed`, `interview.decided`, `arrangements.dates_confirmed`, `acceptance.sent`, `acceptance.received` |
| `recruitment_backlog_by_stage` | Open recruitment items by stage | application | current_state | `stage` | Open applications (not withdrawn, native candidate not cancelled) waiting at each stage at generation time: awaiting screening (no decision, initial screening, hold or information requested), verification in progress (requested, submitted, under review or revision needed), awaiting interview decision (completed booking without a decision), awaiting date confirmation (active case with proposed dates) and awaiting letter receipt (active case with a sent letter). Ignores the period. | none (current state) |

### Internship dashboard

| Metric key | Label | Unit | Semantics | Dimensions | Definition | Source events |
|---|---|---|---|---|---|---|
| `active_interns_by_venture` | Active interns by venture | internship | as_of | `venture`, `venture_id` | Active interns grouped by the venture recorded on the onboarding. Rows add up to active interns. | `internship.started`, `internship.planned_end` |
| `active_interns_by_department` | Active interns by department | internship | as_of | `department`, `department_id` | Active interns grouped by the department recorded on the onboarding. Rows add up to active interns. | `internship.started`, `internship.planned_end` |
| `planned_starts` | Planned starts | internship | period | none | Intern onboardings of any status whose planned start date falls in the period and is not after the as-of date. | `internship.planned_start` |
| `starts_awaiting_onboarding_completion` | Starts awaiting onboarding completion | internship | period | none | Planned starts in the period whose onboarding is not yet onboarded (pending approval, approved or in progress). Explains the gap between planned and actual starts. | `internship.planned_start` |
| `upcoming_starts` | Starting within N days | internship | as_of | `horizon_days` | Approved, in-progress or onboarded onboardings whose planned start is after the as-of date and within the horizon. Horizons are cumulative, so select one horizon and never add rows. | `internship.planned_start` |
| `upcoming_completions` | Completing within N days | internship | as_of | `horizon_days` | Active interns whose planned end date is within the horizon after the as-of date. Horizons are cumulative, so select one horizon and never add rows. | `internship.planned_end` |
| `weekly_reports_due` | Weekly reports due | report | period | none | Weekly report records of interns whose deadline falls in the period and not after the as-of date. Weeks for which an intern never opened a report are not counted. | `weekly_report.due` |
| `weekly_reports_submitted` | Weekly reports submitted | report | period | none | Weekly reports due in the period that are no longer drafts when the snapshot is built. | `weekly_report.due`, `weekly_report.first_submitted` |
| `weekly_reports_on_time` | Weekly reports on time | report | period | none | Weekly reports due in the period whose first submission was no later than the deadline. Later resubmissions do not make a report late. | `weekly_report.due`, `weekly_report.first_submitted` |
| `monthly_presentations_due` | Monthly presentations due | presentation | period | none | Monthly presentation records of interns whose deadline falls in the period and not after the as-of date. | `monthly_presentation.due` |
| `monthly_presentations_completed` | Monthly presentations approved | presentation | period | none | Monthly presentations due in the period whose status is approved when the snapshot is built. | `monthly_presentation.due` |
| `training_completion_rate` | Training completion rate | percent | cohort | none | Completed share of the non-cancelled training assignments of interns due in the period and not after the as-of date; a completion recorded after the as-of date is not counted. | `training.due` |
| `assets_allocated` | Assets allocated | asset | period | none | Native Horilla asset assignments to intern employees made in the period, including assignments made in the native asset screens. | `asset.assigned` |
| `assets_returned` | Assets returned | asset | period | none | Asset assignments of intern employees returned in the period. | `asset.returned` |
| `assets_outstanding` | Assets not yet returned | asset | as_of | none | Asset assignments of intern employees made on or before the as-of date and not returned by then. | `asset.assigned`, `asset.returned` |
| `offboarding_completion_rate` | Offboarding completion rate | percent | cohort | none | Of onboarded interns whose planned end date falls in the period and is not after the as-of date, the share with a completion case completed on or before the as-of date. | `internship.planned_end`, `internship.completed` |

An "intern employee" is an Employee linked to an `InternOnboarding` inside the
filtered onboarding scope.

### Administration dashboard

| Metric key | Label | Unit | Semantics | Dimensions | Definition | Source events |
|---|---|---|---|---|---|---|
| `outstanding_onboarding_tasks` | Outstanding onboarding tasks | task | current_state | none | Required onboarding checklist tasks whose native Horilla task is not done, for onboardings that are not yet onboarded. Read at generation time. | none (current state) |
| `outstanding_onboarding_tasks_by_item` | Outstanding onboarding tasks by checklist item | task | current_state | `checklist_item` | Outstanding onboarding tasks grouped by checklist item code. Rows add up to outstanding onboarding tasks. | none (current state) |
| `documents_by_status` | Erasmus documents by status | document | current_state | `status` | Active Erasmus case documents of active cases grouped by processing status. Read at generation time. | none (current state) |
| `open_document_average_age_days` | Average age of open documents | days | current_state | `status` | Mean days from creation to the as-of date of documents that are not yet complete, by status. A document that needs signatures is complete only when returned; otherwise approved, signed and returned are complete. Numerator is total days, denominator the document count. | none (current state) |
| `workflow_failures` | Workflow failures | event | period | `stage` (company-wide) | Lifecycle events with at least one failed delivery attempt completed in the period (stage delivery) and failed external workflow executions completed in the period (stage execution). Other filters do not apply. | `automation.delivery_failed`, `automation.execution_failed` |
| `workflow_dead_letter_backlog` | Workflow events needing review | event | current_state | none (company-wide) | Lifecycle events currently in the failed (dead-letter) state. Other filters do not apply. | none (current state) |
| `account_provisioning_by_status` | Account provisioning by status | request | current_state | `status` | Intern account provisioning requests grouped by current status. | none (current state) |
| `access_revocation_by_status` | Access revocation by status | request | current_state | `status` | Internship completion access-revocation tasks grouped by current status. | none (current state) |

## Event mappings

Analytics events are derived from authoritative, timestamped Horilla and `tvg_*`
records. The analytics app does not consume `tvg_automation` lifecycle-event
payloads. Tests check every `model.field` below against the live model
registry, so a renamed field cannot silently break a KPI. "Immutable source" is
"no" when the timestamp or the condition's status can change after the event,
which is why a rebuilt snapshot can differ from an earlier revision.

| Event key | Model.field | Condition | Immutable source |
|---|---|---|---|
| `application.submitted` | `tvg_careers.Application.submitted_at` | none | yes |
| `application.withdrawn` | `tvg_careers.Application.withdrawn_at` | none | no |
| `screening.decided` | `tvg_erasmus.ScreeningEvent.occurred_at` | `to_status` is the screening outcome; transitions to initial screening are ignored | yes |
| `verification.requested` | `tvg_erasmus.VerificationCase.requested_at` | status is not `not_required` | no |
| `verification.completed` | `tvg_erasmus.VerificationCase.completed_at` | status is `verified` | no |
| `interview.invited` | `tvg_interviews.InterviewInvitation.invited_at` | none | yes |
| `interview.booked` | `tvg_interviews.InterviewBooking.booked_at` | status is not `cancelled` | yes |
| `interview.completed` | `tvg_interviews.InterviewBooking.completed_at` | status is `completed` | yes |
| `interview.decided` | `tvg_interviews.InterviewDecision.decided_at` | latest decision per application as of the as-of date | yes |
| `arrangements.dates_confirmed` | `tvg_erasmus.ErasmusCaseEvent.occurred_at` | `event_type` is `erasmus.dates_confirmed`; first event per case | yes |
| `acceptance.issued` | `tvg_erasmus.AcceptanceLetter.issue_date` | first letter version per case | yes |
| `acceptance.sent` | `tvg_erasmus.AcceptanceLetter.sent_at` | first letter version per case | yes |
| `acceptance.received` | `tvg_erasmus.AcceptanceLetter.received_at` | first candidate receipt confirmation per case | yes |
| `internship.planned_start` | `tvg_onboarding.InternOnboarding.start_date` | none | yes |
| `internship.started` | `employee.EmployeeWorkInformation.date_joining` | `InternOnboarding` status is `onboarded` and joining date is present | no |
| `internship.planned_end` | `tvg_onboarding.InternOnboarding.end_date` | none | yes |
| `weekly_report.due` | `tvg_reporting.WeeklyReport.deadline_at` | none | yes |
| `weekly_report.first_submitted` | `tvg_reporting.WeeklyReportVersion.submitted_at` | `version_number` is 1 | yes |
| `monthly_presentation.due` | `tvg_reporting.MonthlyPresentation.deadline_at` | none | yes |
| `training.due` | `tvg_training.TrainingAssignment.due_date` | status is not `cancelled` | yes |
| `asset.assigned` | `asset.AssetAssignment.assigned_date` | assigned to an intern employee | yes |
| `asset.returned` | `asset.AssetAssignment.return_date` | assigned to an intern employee | no |
| `internship.completed` | `tvg_offboarding.CompletionCase.completed_at` | state is `completed` | yes |
| `automation.delivery_failed` | `tvg_automation.DeliveryAttempt.completed_at` | outcome is `failed` | yes |
| `automation.execution_failed` | `tvg_automation.WorkflowExecution.completed_at` | status is `failed` | no |

## Automated validation and reconciliation

`tvg_analytics.services.validate_snapshot` runs once, while a snapshot is still
`building`. Every check is stored as an immutable `AnalyticsValidationResult`
with expected and actual values, shown on the snapshot page. Any failed check
sets the snapshot to `failed` and lists the failed check codes in
`last_error`; otherwise the snapshot becomes `validated`.

Integrity and privacy checks:

- `point_count_matches_build`: stored metric points equal the number collected.
- `metric_allowlist`: every metric key is registered in `METRICS`.
- `scalar_metrics_complete`: every metric without dimensions appears exactly
  once.
- `dimension_allowlist`: each point uses only the dimensions declared for its
  metric.
- `filter_allowlist`: the snapshot filters use only the approved filter keys.
- `no_prohibited_tokens`: no dimension or filter key contains a prohibited
  token (see [Privacy and data minimisation](#privacy-and-data-minimisation)).
- `dimension_values_allowed`: dimension values belong to their domains (known
  source codes, screening statuses, document, provisioning and revocation
  statuses, stage codes, horizons and months inside the period); `_id` values
  are integers or empty; other labels must not look like an e-mail address or
  UUID.
- `values_non_negative`: no metric value is negative.

Reconciliation checks:

- `persons_not_greater_than_applications`: unique persons ≤ applications.
- `applications_reconcile_independent_count`: applications equal an
  independent count that omits the opening's recruitment-company join; a
  difference means an opening's Horilla recruitment belongs to another company.
- `applications_by_source_reconciles`, `applications_by_venture_reconciles`,
  `applications_by_department_reconciles`, `applications_by_role_reconciles`
  and `applications_by_month_reconciles`: each breakdown adds up to
  applications submitted.
- `withdrawn_within_applications`: withdrawn ≤ applications submitted.
- `active_interns_by_venture_reconciles` and
  `active_interns_by_department_reconciles`: each breakdown adds up to active
  interns.
- `outstanding_tasks_reconcile`: tasks by checklist item add up to outstanding
  tasks.
- `upcoming_starts_cumulative` and `upcoming_completions_cumulative`: the 7, 30,
  60 and 90 day horizons never decrease.
- `upcoming_completions_within_active`: completions within 90 days ≤ active
  interns.
- `awaiting_starts_within_planned`: starts awaiting onboarding completion ≤
  planned starts.
- `weekly_on_time_within_submitted`, `weekly_submitted_within_due` and
  `monthly_completed_within_due`: report subsets.
- `interviewed_within_completed_interviews`: applications interviewed ≤
  interviews held.
- `<metric>_rate_consistent` for each rate (for example
  `verification_completion_rate_rate_consistent`): value equals
  100 × numerator ÷ denominator rounded to two decimals, numerator ≤
  denominator, and the value is within 0–100.
- `<metric>__<stage or status>_average_consistent` for each average (for
  example `stage_average_duration_days__interview_to_decision_average_consistent`):
  value equals numerator ÷ denominator within 0.01.

Funnel inequalities between stages (for example accepted ≤ offers, offers ≤
selected or started ≤ accepted) are deliberately not checked. With
event-period counting a candidate can be offered in one period and accept in
the next, and the stages count different units, so such a check would fail on
correct data. Subset checks are applied only where both numbers describe the
same population in the same snapshot.

## Snapshot lifecycle

A snapshot freezes the aggregate metric points for one company, filter set,
period and as-of date.

| Status | Meaning | Next statuses |
|---|---|---|
| `building` | Created; metrics are being collected. | `validated`, `failed` |
| `validated` | Every validation check passed. Can be published. | `exported`, `superseded` |
| `failed` | Collection raised an error or a check failed. Terminal; a retry creates a new revision. | none |
| `exported` | Published through the configured exporter. The reporting view shows it. | `superseded` |
| `superseded` | Replaced by a newer revision. Terminal. | none |

- **Fingerprint and revisions.** The fingerprint is a SHA-256 hash of the KPI
  schema version, company, filters (including the period) and as-of date. Each
  build for a fingerprint gets the next revision number.
- **Build.** Building requires the `generate_analytics_snapshot` permission and
  an employee of the same company. Without rebuild, the latest `validated` or
  `exported` revision for the fingerprint is returned unchanged. A `failed`
  revision never blocks a retry.
- **Rebuild.** "Rebuild snapshot with current data" creates a new revision even
  when a validated or exported one exists, for example after late data or after
  a status changed. When the new revision validates, older `validated`
  revisions of the fingerprint become `superseded`. An older `exported`
  revision stays visible in the reporting view until the new revision is
  exported.
- **Collection failure.** An exception during collection stores no points,
  sets `failed` and records only the exception type in `last_error`.
- **Export (publication).** Exporting requires the `export_analytics_snapshot`
  permission, an employee of the same company and a `validated` snapshot. The
  exporter runs outside the database transaction. Every attempt is stored as an
  immutable `AnalyticsExportAttempt`; a repeated export after success returns
  the first successful attempt. The idempotency key is
  `analytics:<snapshot public id>:v<schema version>`. On success the snapshot
  becomes `exported` and older `exported` revisions of the same fingerprint
  become `superseded`. A failed attempt leaves the snapshot `validated` so it
  can be retried.
- **Immutability.** Company, period, as-of date, filters, fingerprint, revision,
  schema version and generator cannot change after creation; status only moves
  forward. Metric points, validation results and export attempts cannot be
  edited, and the Django admin for them is read-only.

## Privacy and data minimisation

- **Aggregate only.** Metric collection reads counts, dates and allow-listed
  labels (venture, department and job-position names, source, status and stage
  codes, checklist item codes). No name, contact detail, document, file, free
  text, rating, feedback or protected characteristic is read into a metric, and
  no metric has a person-level drill-down.
- **Prohibited tokens.** Dimension keys, filter keys and exported batch fields
  must not contain any of these `_`-separated words:
  `name`, `email`, `phone`, `mobile`, `address`, `birth`, `dob`, `age`,
  `nationality`, `citizenship`, `country`, `gender`, `sex`, `race`,
  `ethnicity`, `ethnic`, `religion`, `disability`, `medical`, `health`,
  `marital`, `salary`, `payroll`, `iban`, `identity`, `passport`, `file`,
  `candidate`, `person`, `employee`, `user`. Words are matched whole, so a
  metric key such as `documents_by_status` is not affected. The rule is checked
  at validation (`no_prohibited_tokens`) and again by the exporter before
  publication.
- **No identity documents, medical data or protected characteristics.**
  Verification evidence, identity documents, medical data, nationality,
  citizenship, country, gender, sex, date of birth, ethnicity, religion,
  disability, marital status and passport data are never queried
  (`PROTECTED_CHARACTERISTIC_TOKENS`). Horilla's native "Diversity Snapshot"
  report and the gender filter of its native recruitment funnel are outside
  `tvg_analytics` and must not be copied into these KPIs or into Metabase.
- **Small-cell suppression.** A value that could single out an individual is
  flagged `is_small_cell` when the snapshot is built
  (`services.is_small_cell`), using the threshold
  `TVG_ANALYTICS_MIN_CELL_SIZE` (default 5; a value below 1 fails the
  `tvg_analytics.E003` system check). The threshold used is stored in the
  snapshot's `source_counts.min_cell_size`. The rule is:
  - counts in person-linked units (every count unit except `event`) are small
    when `0 < value < threshold` and the point has dimensions or the snapshot
    has a venture, department, role or source filter; an unfiltered headline
    count is never suppressed;
  - rates and averages are small when `0 < denominator < threshold`;
  - workflow `event` counts are never suppressed, because they describe
    systems, not people;
  - zero is never suppressed.

  Suppression applies at the external boundary only: the exported batch and
  the reporting view replace `value`, `numerator` and `denominator` with
  nothing and set `suppressed`. Validation, the PeopleOps dashboards and the
  Horilla reports use exact values, because they are permission-checked
  internal views; the snapshot page shows which points are withheld from
  Metabase.
- **Residual disclosure risk.** Suppression reduces, but does not remove,
  re-identification risk. A suppressed breakdown cell can sometimes be derived
  from an unsuppressed total minus the other cells. Metabase users must not
  republish breakdowns outside the authorised staff group.
- **Current-state metrics** describe work queues (tasks, documents, requests,
  backlog stages, failed workflow events), not people, and are read at
  generation time.

## Human-decision boundary

Analytics describes decisions that humans have already recorded. Neither
`tvg_analytics` nor any AI feature makes or enacts a hiring, rejection,
verification or offer decision. The app writes only its own snapshot,
validation and export records; it never changes an application, screening,
verification, interview, Erasmus case, onboarding or employee record, and it
produces no per-person score or ranking. Nationality and the other protected
characteristics listed above are never used. Decision counts such as
`rejected_candidates` are process indicators, not targets for an individual
decision.

## Horilla standard reports

`tvg_analytics` registers two reports in Horilla's standard report catalogue
(`report.registry.register`) when the `report` app is installed. No upstream
report file is edited.

| Slug | Name | Content |
|---|---|---|
| `tvg-peopleops-executive` | PeopleOps executive summary | Executive cards and details, upcoming starts and completions, active interns by venture and department, and the administration breakdowns. |
| `tvg-recruitment-pipeline` | CareerHub recruitment pipeline | Recruitment cards and details, applications by source, venture, department, role and month, screening outcomes, stage durations and backlog. |

Both reports are in the `talent` domain, require the
`tvg_analytics.view_peopleops_analytics` permission (subject to Horilla's report
access matrix), offer the department and job position filters, and have no
drill-down and no export model. The company is always Horilla's selected
company; when "all companies" is selected the report shows "Select a single
company to view PeopleOps analytics." The period end is capped at today and is
also the as-of date. Horilla's report filter set has no venture or CareerHub
source filter; use the PeopleOps dashboards for those. Values are exact (no
small-cell suppression); Horilla report exports and subscriptions therefore
carry the same values, so grant them only to roles that may see the
dashboards.

Horilla's native `recruitment-funnel` report answers a different question:

| Aspect | Native `recruitment-funnel` | TVG KPIs |
|---|---|---|
| Record counted | Horilla `Candidate` | CareerHub `Application` (one Candidate per application) and `Person` for unique applicants |
| Date | `Candidate.created_at` | `Application.submitted_at` and each stage's own event timestamp |
| Inclusion | Active candidates only | All submitted applications, withdrawn ones included |
| Stages | Snapshot by Horilla stage and stage type | Separate event KPIs, decisions, durations and a current backlog |
| Sources | Horilla `Candidate.source` buckets (referral, application, software, other); every CareerHub candidate is stored as `application` | The CareerHub attribution allow-list on `Application.source` |
| Privacy | Offers a gender filter and a named drill-down | No protected-characteristic filter or dimension, no drill-down |

The two reports therefore do not reconcile in general; they agree on totals
only when every candidate is an active CareerHub application created and
submitted on the same day.

## Metabase reporting contract

Metabase reads only the PostgreSQL view
`tvg_analytics_reporting.metric_point_v1`, created by migration
`tvg_analytics.0002_snapshot_revisions_reporting_view` in the dedicated schema
`tvg_analytics_reporting`. The view returns one row per metric point of every
active snapshot whose status is `exported`; `building`, `validated`, `failed`
and `superseded` snapshots are not visible. The SQLite test equivalent is
`tvg_analytics_reporting_metric_point_v1`. Access, roles and the Metabase set-up
are in `docs/integrations/METABASE_REPORTING.md`.

| Column | Meaning |
|---|---|
| `snapshot_id` | Snapshot public UUID (no internal row id is exposed). |
| `schema_version` | KPI schema version of the snapshot. |
| `snapshot_revision` | Revision number within the fingerprint. |
| `company_id` | Horilla company id. Always filter on one company. |
| `period_start` | Snapshot period start (local date). |
| `period_end` | Snapshot period end (local date). |
| `as_of_date` | Snapshot as-of date. |
| `generated_at` | When the snapshot was generated; current-state metrics are read at this time. |
| `exported_at` | When the snapshot was published. |
| `filter_venture_id` | Venture filter of the snapshot, or empty. |
| `filter_department_id` | Department filter of the snapshot, or empty. |
| `filter_role_id` | Role (job position) filter of the snapshot, or empty. |
| `filter_source` | Source filter of the snapshot, or empty. |
| `is_unfiltered` | True when none of the four filters above is set. |
| `dashboard` | Home dashboard of the metric. |
| `metric_key` | Registered metric key. |
| `dim_source` | Source dimension value. |
| `dim_month` | Month dimension value (`YYYY-MM-01`). |
| `dim_screening_outcome` | Screening outcome dimension value. |
| `dim_venture` | Venture name dimension value. |
| `dim_venture_id` | Venture id dimension value. |
| `dim_department` | Department name dimension value. |
| `dim_department_id` | Department id dimension value. |
| `dim_role` | Job position name dimension value. |
| `dim_role_id` | Job position id dimension value. |
| `dim_stage` | Stage dimension value. |
| `dim_status` | Status dimension value. |
| `dim_horizon_days` | Horizon dimension value (`7`, `30`, `60`, `90`). |
| `dim_checklist_item` | Checklist item code dimension value. |
| `value` | Metric value; empty when suppressed. |
| `numerator` | Rate or average numerator; empty when suppressed or not applicable. |
| `denominator` | Rate or average denominator; empty when suppressed or not applicable. |
| `suppressed` | True when the point is a small cell and its measures are withheld. |

Rules that prevent double counting:

1. The view holds every published filter combination, period and as-of date
   side by side. Choose exactly one: filter `company_id`, then either
   `is_unfiltered` = true or one exact combination of `filter_venture_id`,
   `filter_department_id`, `filter_role_id` and `filter_source` (an empty
   filter column means "all", not "unassigned"), then one `period_start`,
   `period_end` and `as_of_date`.
2. Within one fingerprint only the latest exported revision is visible, but
   snapshots with overlapping periods (for example a month and the quarter
   containing it) are different fingerprints. Never sum across periods.
3. Never sum different `metric_key` values. Units differ (see
   [Counting units](#counting-units-and-data-quality-rules)).
4. A scalar metric has all `dim_*` columns empty. A breakdown metric has one row
   per dimension value; use the breakdown only to split its own total.
5. Horizons are cumulative: filter one `dim_horizon_days` value.
6. `initial_screening_outcomes` rows overlap by design; do not add them.
7. Do not rebuild a total from breakdown rows when any row is `suppressed`; use
   the scalar metric instead.
8. Rates and averages must not be averaged across rows; recompute from
   `numerator` and `denominator` only where neither is suppressed.

Versioning: `metric_point_v1` is a stable contract. Adding a metric key or a
new dimension value is compatible. Adding, removing or renaming a column,
changing a column's meaning, or adding a dimension creates
`tvg_analytics_reporting.metric_point_v2` in a new migration alongside v1; v1 is
never edited in place and is removed only after every Metabase question has
moved to v2.

## Synthetic demonstration data

The focused analytics test suite builds conspicuously synthetic companies,
people, applications, interviews, arrangements, interns, reports, training,
assets, workflow failures and completion records. Addresses use reserved
`example.invalid` domains, and tests use only the synthetic export adapter or
the local reporting view. The reconciliation fixture deliberately gives one
person two applications and creates another company's records, proving both the
counting-unit distinction and tenant isolation. `test_smoke` completes the
executive dashboard, recruitment dashboard, validated snapshot, explicit
publication and reporting-view read without sending mail or calling a network.

For browser demonstrations, use the existing non-production-only
`seed_tvg_career_demo` command for synthetic openings, then submit only
synthetic applications through the normal test/local workflow. There is no
analytics command that writes a full lifecycle into an existing environment;
that avoids accidentally creating Employee, legal-document, asset or
offboarding records outside an isolated test database.

## Change log

| Date | Schema version | Change |
|---|---|---|
| 2026-10-27 | 1 | Pre-release aggregate-snapshot prototype. Superseded before the deployable migration baseline. |
| 2026-10-28 | 2 | Phase 17 baseline: snapshot revisions and rebuild; small-cell flag; registry-wide units, semantics, dimensions and event mappings; named reconciliation checks; curated `tvg_analytics_reporting.metric_point_v1` view of exported snapshots only; Horilla standard report registration; this specification. |
