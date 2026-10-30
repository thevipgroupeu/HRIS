# ADR-013: Internship completion and offboarding gate

**Status**: Accepted for the Phase 14 implementation slice  
**Date**: 2026-10-27  
**Related**: ADR-009, ADR-010, ADR-011, ADR-012

## Context

Horilla already owns the employee offboarding pipeline, offboarding tasks,
employee status and asset-return records. THEVIPGROUP's Erasmus+ completion also
depends on records outside native offboarding: Phase 10 university-specific After
Internship documents, Phase 11 account/access grants and Phase 13 final reporting.
Completion certificates, access-revocation proof, explicit exceptions and
retention review are not represented by those modules.

The workflow must not report completion while mandatory evidence is missing and
must never erase historical recruitment, internship or legal records. External
identity providers may not offer an approved revocation API.

## Decision drivers

- Preserve Horilla as the source of truth for Employee, OffboardingEmployee and
  AssetAssignment.
- Reuse Erasmus document requirements for university and Learning Agreement
  After Mobility documents.
- Keep completion auditable, company-scoped and fail-closed.
- Require approved templates and recorded human signatures for generated
  completion and asset-return documents.
- Support provider-neutral access revocation without claiming unsupported APIs.
- Make retention configurable without implementing automatic legal-record
  deletion.

## Considered options

### Extend Horilla offboarding models directly

This would make the native pipeline aware of Erasmus documents, reporting and
provider-specific access systems. It offers one data structure but creates a
large upstream compatibility burden and changes generic employee-offboarding
semantics.

### Build a replacement offboarding workflow

This offers total control but duplicates native employees, stages, tasks and
asset returns. It would split operational truth and violate the fork's reuse
rules.

### Add a TVG completion gate linked to native records

This keeps native models authoritative and adds only the cross-domain state,
evidence and policy that Horilla lacks. It requires explicit synchronization but
keeps the boundary reviewable and reversible.

## Decision

Create `tvg_offboarding` with a one-to-one completion case linked to Phase 11's
`InternOnboarding` and Horilla's `OffboardingEmployee`.

- Starting a case creates or reuses the configured company offboarding pipeline
  entry. It never creates a second Employee or removes recruitment history.
- Mandatory completion requirements point to native reporting, Erasmus document,
  asset and access-revocation evidence. State is derived as Upcoming, In
  Progress, Awaiting Documents, Awaiting Assets, Awaiting Access Revocation or
  Completed.
- Required university documents remain `ErasmusCaseDocument` records generated
  from approved university-specific requirements. Optional forms remain optional.
- Native `AssetAssignment.return_date`, return status and serialized AssetItem
  state remain authoritative. The completion gate only links return evidence and
  approved handover documentation.
- Access revocation uses a provider adapter. The default provider is disabled and
  creates a tracked manual task. A human with the dedicated permission records
  non-secret proof before a system is considered revoked.
- Completion and asset-return PDFs are generated only from immutable,
  human-approved company template versions. Signature rows are human
  attestations; no company identity, legal text or signature is invented.
- Mandatory requirements may be bypassed only by a separate authorized exception
  containing a reason, actor and timestamp. Exceptions do not rewrite source
  records.
- Versioned retention policies create review schedules for TVG and linked
  university documents. The scheduler may mark a review due but never deletes a
  record or file automatically.
- Final completion is an explicit HR action. It archives the native pipeline
  entry and deactivates current staff access while preserving all linked history.

## Consequences

### Positive

- Horilla offboarding and assets remain reusable by non-intern employees.
- University forms, report reviews and access proof remain in their owning
  modules while one gate enforces completion.
- Unsupported identity APIs fail closed and remain operationally visible.
- Retention review is configurable and auditable without destructive automation.

### Negative

- Completion state must be refreshed when source records change.
- Administrators must configure a native offboarding process, approved document
  templates, retention policies and HR recipients before automation can start.
- Real identity/mail providers still need separately approved adapter contracts.

### Risks and mitigations

- **Stale completion projection**: every state-changing service refreshes the
  gate, and the scheduler performs reconciliation.
- **Premature closure**: database-locked completion validates every mandatory
  requirement, outstanding native asset and access task.
- **Sensitive revocation evidence**: proof files use private storage and mediated
  downloads; logs and audit payloads contain identifiers, not file contents or
  credentials.
- **Incorrect deletion**: retention schedules expose review dates only; no
  automatic delete operation exists.
