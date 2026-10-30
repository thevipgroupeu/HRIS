# ADR-010: Intern onboarding and identity provisioning

Date: 2026-10-27  
Status: Accepted for the Phase 11 implementation slice

## Context

An accepted Erasmus candidate must become an intern without replacing Horilla's
Candidate, onboarding, Employee, user, permission or Asset records. The stable
person identity and personal address must remain distinct from a work identity,
staff identifiers and a provisioned company address. Zoho may currently be used
operationally, but no authorized mailbox API contract has been approved.

## Decision

- `tvg_onboarding.InternOnboarding` is a OneToOne workflow link from the approved
  Erasmus case to the existing Person, Candidate and eventual Employee.
- The trigger requires acceptance receipt, immutable confirmed dates, HR-approved
  arrangements and Phase 10 Before Mobility completion. A separate human approval
  is required before Employee conversion or account provisioning completion.
- Checklist rows link to native `OnboardingTask`, `CandidateTask`, `CandidateStage`
  and `OnboardingStage` records. Completion remains visible in Horilla onboarding.
- The Employee uses the confirmed company work email and generated employee badge.
  `Person.primary_email` remains unchanged; `Person.employee` and
  `Candidate.converted_employee_id` preserve identity and recruitment history.
- Company/year sequences generate separate configurable intern and employee IDs.
  Existing identifiers are never rewritten.
- Identity/mail provisioning is behind an adapter selected by company policy.
  Manual, Zoho and self-hosted keys fail closed by default. An unsupported provider
  creates a tracked manual IT task; credentials are never stored in workflow data.
- Department/venture access is granted only from a human-approved profile. The
  approved group-id snapshot is immutable and becomes company-scoped
  `CompanyGroupAssignment` rows for the native Horilla user.
- Equipment allocation creates native `AssetAssignment` records against an
  available `AssetItem`, retaining serial number, allocation date, borrower and
  return state in Horilla Assets.
- Asset acknowledgments are rendered only from immutable, approved, editable text
  templates. Generated PDFs are stored privately and require an authorized
  PeopleOps download. No signature or legal entity text is invented.
- Welcome and arrival communications are schedule records only. This phase does
  not send email; durable delivery remains a Phase 6 automation concern.

## Consequences

The extension stays upstream-compatible and does not change core model semantics.
Onboarding can be retried idempotently without duplicate Employee records. Real
mailbox automation remains unavailable until an authorized adapter, secrets
boundary, sandbox behavior and operational contract are reviewed and configured.
