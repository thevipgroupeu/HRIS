# ADR-004: Reusable Candidate Profile and Application Snapshots

## Status

Accepted for the Phase 5 increment on 2026-10-25.

## Context

CareerHub and PeopleOps are separate deployables and databases. Applicants need
one reusable profile and multiple applications, while Horilla models each
application as a separate `Candidate` in one `Recruitment`. Mutable profile and
document data must not silently rewrite evidence used for an earlier hiring
decision.

## Decision

- CareerHub owns `CandidateProfile`, versioned `CandidateProfileDocument`
  records and `CandidateApplication` drafts. They belong to exactly one public
  `CandidateAccount`; every view resolves ownership from `request.user`.
- A submitted CareerHub application contains an immutable JSON profile copy,
  versioned question/answer copy, and physically independent CV/cover-letter
  PDF or DOCX copies.
  Replacing a reusable file or editing a profile therefore affects only later
  submissions.
- CareerHub freezes that evidence locally before invoking PeopleOps. If the
  service call fails or its response is lost, retry uses the exact frozen
  profile, answers and files with the same application UUID rather than taking
  a new snapshot of subsequently edited profile data.
- CareerHub permits one application per account/opening. This is intentionally
  stronger than “one active application”: Phase 2 PeopleOps already enforces
  one `Person`/`JobOpening`, and Horilla enforces one candidate email per
  recruitment. Withdrawal is terminal unless a later, explicit reapplication
  policy changes all three invariants.
- Submission calls a dedicated bearer-authenticated PeopleOps command. The
  CareerHub application UUID is the idempotency key. PeopleOps verifies the
  configured legal employer, stable Person/email pair, opening publication and
  application dates again before creating an existing Horilla `Candidate` and
  Phase 2 `tvg_careers.Application` link.
- One Person applying to two openings produces two independent Horilla
  Candidate records, stages and histories. No replacement Candidate,
  Recruitment, Employee or Leave model is introduced.
- Withdrawal is owner-scoped and idempotent. PeopleOps timestamps the link and
  sets `Candidate.canceled`, allowing Horilla's existing model behavior to move
  it to a cancelled stage and retain audit history.
- The status API returns only an allow-list: friendly status/history, interview
  date/time/completion, offer state and onboarding progress counts. It excludes
  reviewer comments, interview descriptions/interviewers, ratings, AI output,
  staff identities and other applicants.

## Files and storage

Candidate uploads accept PDF and DOCX files up to 5 MB. CareerHub verifies the
extension, declared content type and document structure, and rejects known
active content. Files use a private storage root with no media URL; downloads
stream only after owner-object lookup and use `no-store`/`nosniff`. CareerHub
and PeopleOps retain separate physical copies because their data ownership and
access policies differ.

Structural document checks are not malware scanning. Production launch still
requires an approved quarantine/scanner workflow and retention/erasure policy.
Development and automated tests use synthetic PDFs and temporary storage only.

## Consequences

- CareerHub needs a persistent private-media volume in addition to its database
  and Redis. Backups and erasure must cover database rows and both reusable and
  snapshot objects.
- An interrupted submission remains a frozen local draft. Retrying the same
  UUID is idempotent in PeopleOps and cannot create another Candidate or change
  the application-time evidence.
- Candidate-facing history is a data-minimised projection, not the authoritative
  HR audit log. Horilla remains authoritative for stage/history decisions.
- Service-token rotation now includes a third credential dedicated to
  application commands/status; it must differ from catalogue-read and identity
  provisioning credentials.
