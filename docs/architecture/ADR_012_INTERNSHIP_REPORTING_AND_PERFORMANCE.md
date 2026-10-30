# ADR-012: Internship reporting and human performance review

Date: 2026-10-27  
Status: Accepted for the Phase 13 implementation slice

## Context

Horilla already owns Employee, reporting-manager, Project, Task, PMS feedback,
permissions and in-app notifications. It does not provide the internship-specific
weekly report, monthly presentation, revision, evidence and rubric history needed
by THEVIPGROUP. A parallel employee or project model would split business truth,
while treating AI prose as a performance decision would create an unacceptable
automated-employment decision path.

## Decision

- `tvg_reporting` links every item to native `Employee`, `Project` and optional
  completed `Task` records. Intern eligibility and confirmed dates are read from
  the Phase 11 onboarding link, or from native Intern work information for
  established records.
- A company reporting policy owns weekly/monthly deadlines and reminder offsets.
  Submitted content is copied to an immutable numbered version. Supervisor
  feedback attaches to one version, so a revision creates a new version without
  rewriting the earlier submission or review.
- Direct reporting managers can review their own interns. Broader review and
  management status require explicit `tvg_reporting` permissions, always with a
  legal-employer check. Interns can edit only their own draft/revision-required
  records.
- Rubrics are company-scoped and versioned. An authorized human must approve a
  version before review; approved rubrics are immutable. Criteria explicitly
  distinguish `report_writing` from `internship_performance`, and reviewer
  adjustments require a reason.
- PDF/PPTX presentation uploads use a reporting-only private storage root,
  structural/type/active-content checks, opaque paths, digests and mediated
  `no-store` downloads. Production malware scanning and retention remain separate
  controls and are not claimed by this implementation.
- Reminders and review notifications are deduplicated records dispatched through
  Horilla notifications by the single `run_scheduler` process. No outbound email
  is sent by this workflow.
- AI is an optional adapter, disabled by default. Its allow-listed output is a
  summary, missing-evidence list and supervisor briefing with provider, model and
  prompt versions. It returns no performance outcome, discipline recommendation
  or Intern-of-the-Month decision and cannot approve a report. Human adjustments
  are recorded separately and immutably.
- Management views expose workflow counts and overdue status, not report bodies
  or unnecessary personal information.

## Consequences

The Phase 13 workflow adds internship-specific lifecycle records while leaving
Horilla's employee, project, task and PMS semantics intact. Files need a durable
private volume mounted by PeopleOps, PeopleHub and the scheduler. A future real
AI provider, email channel, malware scanner or scoring policy requires an
approved adapter/contract and does not change existing submissions silently.
