# ADR-016: PeopleOps analytics and Metabase reporting boundary

**Status**: Accepted for Phase 17  
**Date**: 2026-10-28  
**Related**: ADR-001, ADR-011, ADR-014, ADR-015

## Context

Horilla and the existing `tvg_*` applications hold the authoritative records
for recruitment, Erasmus arrangements, onboarding, reporting, training,
assets, automation and completion. Horilla already has useful generic reports,
but those reports do not consistently distinguish a permanent person from an
application, interview, acceptance letter, accepted case and internship that
actually started. They also do not provide a least-privilege Metabase surface
covering the THEVIPGROUP lifecycle.

Metabase must not receive broad read access to the HR database. Candidate
documents, identity evidence, personal contact data, medical data, payroll data
and protected characteristics are unnecessary for these operational KPIs.
Analytics must remain descriptive; it must not rank applicants or make hiring
decisions.

## Decision drivers

- Keep Horilla and the domain extensions as the only systems of record.
- Give every KPI one explicit counting unit, timestamp and period semantic.
- Reuse the Horilla standard-report catalogue and access matrix where useful.
- Enforce company and object authorization in Django, not at the tunnel layer.
- Publish only reconciled, privacy-minimised aggregates to Metabase.
- Make publication an explicit, permission-checked human action.
- Preserve prior published results when late data requires a rebuilt revision.
- Keep development and tests free of real external side effects and secrets.

## Considered options

### Give Metabase read access to Horilla tables

Rejected. Even a read-only login would expose far more personal and legal data
than required, couple dashboards to unstable operational schemas and allow
unreviewed joins that defeat counting and tenant-isolation rules.

### Export person-level rows to a separate warehouse

Rejected for this phase. It adds identity duplication, retention and breach
scope without being needed for the requested executive and recruitment
dashboards.

### Put every KPI directly into the upstream `report` app

Rejected. The lifecycle spans isolated THEVIPGROUP applications and needs
versioned snapshots, reconciliation, small-cell controls and a Metabase
publication boundary. Editing upstream report semantics would also make Horilla
upgrades harder.

### Isolated aggregate analytics extension with curated reporting view

Selected. `tvg_analytics` owns only KPI definitions, aggregate snapshots,
validation results and publication attempts. It registers two aggregate-only
definitions with Horilla's report catalogue and exposes a dedicated staff UI.

## Decision

- `tvg_analytics.definitions` is the versioned registry for metric keys,
  counting units, period semantics, allowed dimensions and event mappings.
- Live dashboards query authoritative domain models using one company derived
  from the authenticated employee. Filters are limited to venture, department,
  role, period and recruitment source.
- Snapshot creation stores immutable aggregate metric points. Named
  reconciliation and privacy checks must all pass before a snapshot becomes
  `validated`. A rebuild creates a new revision; it never edits a prior result.
- `unique_persons`, `applications_total`, `interviews_total`, `offers_issued`,
  `accepted_candidates` and `internships_started` remain separate KPIs with
  separate units and timestamp rules. They are not presented as a necessarily
  decreasing same-period funnel.
- Candidate or employee identifiers, contact fields, document/file fields,
  medical fields, payroll fields and protected characteristics are not valid
  filters, dimensions or reporting columns. Static and behavioral tests enforce
  that boundary.
- Counts below the configured minimum cell size are marked and their measures
  are withheld from export. This is defense in depth, not authorization.
- Publication requires `tvg_analytics.export_analytics_snapshot` and a valid
  snapshot. The default adapter is disabled. The approved reporting-view
  publisher performs no network call and records an immutable, idempotent
  attempt.
- PostgreSQL migration `0002_snapshot_revisions_reporting_view` creates
  `tvg_analytics_reporting.metric_point_v1` with `security_barrier`, revokes
  PUBLIC access and grants SELECT only when the DBA-managed
  `tvg_analytics_reader` group role already exists. No login role or password is
  created by Django.
- Metabase receives a separate login role, created out of band, that can read
  only the curated view. It never receives access to the `tvg_analytics_*`
  tables or other Horilla tables. Metabase's own permissions and a private
  network/Cloudflare Access protect its UI; neither replaces Django or database
  authorization.
- Existing Horilla reports remain available. The extension registers aggregate
  executive and recruitment reports without enabling person-level drill-down.
- Analytics is descriptive only. No metric, Metabase question or automation may
  select, reject, discipline, rank or otherwise decide an employment outcome.

## Consequences

Positive:

- Executive and recruitment metrics have stable, testable business meanings.
- Metabase has a narrow data contract and cannot browse operational HR tables.
- Reconciliation failures and publication attempts are visible and auditable.
- Horilla remains upgradeable because upstream Candidate, Employee and report
  models are not replaced or semantically changed.

Trade-offs:

- Live dashboard queries span several applications and should be monitored as
  data volume grows; scheduled snapshots or a separate analytics database can
  be added behind the same contract.
- The reporting view contains all publishing companies. In Metabase Community
  Edition, access must be limited to viewers authorized for every included
  company, or deployment must publish one company per reporting boundary.
- Current-state backlog metrics can change between revisions even for the same
  historical period; the generated timestamp and revision therefore matter.
- Small-cell suppression can make low-volume breakdowns unavailable in
  Metabase. Authorized staff can still inspect operational records through the
  source workflows, subject to their normal permissions.

## Follow-up

- Review the KPI registry and reporting-view contract whenever a lifecycle
  source changes.
- Create `metric_point_v2` alongside v1 for any breaking reporting-column
  change; do not silently edit the existing view contract.
- If a separate analytics database is introduced, use the same validated batch
  contract, idempotency key and publication audit trail.
- Periodically audit database grants and Metabase group permissions against
  `docs/integrations/METABASE_REPORTING.md`.
