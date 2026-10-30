# ADR-006: Erasmus+ pre-interview verification

**Status:** Accepted for the Phase 7 verification increment on 2026-10-26.

## Context

THEVIPGROUP must verify identity, student status and university confirmation
after a human initial screen and before an interview invitation. Horilla already
owns Recruitment, Candidate, stages, interview schedules and candidate document
requests, but its document records do not provide private quarantine storage,
versioned evidence, a checklist snapshot, retention metadata or a central
interview eligibility policy.

CareerHub and PeopleOps are separate deployables and databases. CareerHub must
never read Horilla tables or receive HR-only notes, reviewers or ratings.

## Decision

Add the isolated `tvg_erasmus` PeopleOps app. It extends, rather than replaces,
the native recruitment domain:

- `ScreeningDecision` and immutable `ScreeningEvent` belong to one existing
  `tvg_careers.Application`. Human actions are initial screening, shortlisted,
  rejected, hold and information requested.
- `VerificationChecklist` belongs to one Horilla `Recruitment` and contains
  configurable requirement templates. An Erasmus+ checklist cannot be used
  unless identity, student-status and university categories are all required.
- Shortlisting snapshots the active templates into a one-per-Application
  `VerificationCase` and `VerificationRequirement` rows. Each requirement links
  to native `CandidateDocumentRequest` and `CandidateDocument` metadata so the
  Horilla candidate record remains the pipeline authority.
- Actual evidence bytes live only in a dedicated non-public storage root and
  are represented by immutable, UUID-addressed `VerificationEvidence` versions.
  Native `CandidateDocument.document` is deliberately not populated with
  identity evidence.
- Immutable audit events record state transitions without document contents.
  Candidate-safe notification events are idempotent; they do not send mail in
  this increment.

The required sequence is enforced by transactional services:

```text
Application submitted
  -> human initial screening
  -> shortlisted
  -> verification requested
  -> candidate evidence submitted and scanned
  -> human document review
  -> every required item verified
  -> interview eligible
```

A rejected, withdrawn, inactive, unshortlisted or incomplete application is not
interview eligible. Pre-save enforcement covers both `InterviewSchedule`
creation/update and moving a linked Horilla Candidate to an interview-type
stage. The native schedule form invokes the same policy for usable form errors.
Candidates without a CareerHub `Application` remain on Horilla's upstream
behavior; this prevents the extension from breaking unrelated legacy records.

Non-Erasmus openings without a checklist receive an explicit `not_required`
case after shortlisting. Erasmus+ openings fail closed if the three-category
checklist is missing or incomplete. There is no override command in this
increment, so incomplete verification cannot be bypassed through the UI or API.

## Authorization and service boundary

- Screening and review require an authenticated Horilla Employee, the relevant
  custom permission, legal-employer scope, and assignment as a recruitment or
  stage manager. Superusers still require a linked Employee so decisions always
  have an accountable actor.
- CareerHub obtains only a candidate-safe projection through the existing
  application-status boundary. It uploads through a separate, exact bearer
  credential and must provide the owning Person UUID, Application UUID and
  requirement UUID. Mismatched owners return a generic not-found response.
- CareerHub resolves its local Application from the authenticated candidate
  account before calling PeopleOps. Candidate and employee numeric identifiers,
  reviewer identities, internal notes and evidence paths are never projected.
- HR downloads require object authorization at request time and a clean scan.
  Responses are attachment-only with `private, no-store`, `nosniff` and a
  restrictive content security policy. Nginx has no mount or alias for the
  verification volume.

## Upload, scanning and retention

Evidence accepts configured PDF/JPEG/PNG types only, with structural and MIME
checks and an absolute 5 MB cap. Images also have a decompression/dimension
limit. The default scanner adapter is deliberately fail-safe: it returns
`pending`, which prevents review and download. Production must configure and
approve a real scanner adapter before candidate uploads are enabled. The fake
clean adapter exists only for synthetic tests.

The checklist defines retention from 1 to 730 days (default 90). Verification
approval/rejection, application withdrawal and screening rejection start the
retention clock. `purge_expired_verification_evidence` removes bytes while
retaining minimal hashes, timestamps and audit metadata. This command must be
scheduled only after privacy-owner approval and tested backup/restore and
erasure procedures. Document contents are never included in events, logs or AI
systems.

## Consequences

- Horilla remains authoritative for Recruitment, Candidate, pipeline history,
  interview schedules and document-request metadata.
- CareerHub remains isolated and stores no reusable copy of verification
  evidence; it streams the validated upload to PeopleOps.
- A dedicated private volume and a fourth service credential are operational
  requirements.
- A scanner callback/worker, notification delivery worker, production retention
  schedule and DPIA approval remain production-readiness work. The disabled
  scanner intentionally makes interviews impossible rather than silently
  trusting unscanned evidence.

## Verification

Automated coverage includes human screening actions, mandatory checklist
categories, missing/rejected/corrected evidence, immutable versions, clean-scan
requirements, direct schedule and stage gating, owner and tenant isolation,
manager permissions, candidate-safe projections, reminders without email and
retention deletion. CareerHub tests cover dashboard next actions, upload
validation, cross-account denial and data-minimized security events.
