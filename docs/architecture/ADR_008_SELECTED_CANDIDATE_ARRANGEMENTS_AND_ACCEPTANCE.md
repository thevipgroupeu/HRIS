# ADR-008: Selected-candidate arrangements and formal acceptance

## Status

Accepted for Phase 9 implementation on 2026-10-26.

## Context

Horilla records the selected Candidate, Recruitment, interview schedule and
human interview decision, but it does not model candidate-confirmed Erasmus+
dates, university and curriculum arrangements, short-duration exceptions, HR
approval snapshots or versioned acceptance documents. CareerHub and PeopleOps
remain separate runtimes and databases. Candidate actions therefore need a
narrow owner-scoped command/download boundary, while legal issuer data and
authorization remain private PeopleOps responsibilities.

THEVIPGROUP normally requires an internship of at least three calendar months.
Existing or candidate-confirmed dates must not be silently rewritten to meet
that rule. Acceptance letters also cannot be built from guessed company,
registration or signatory data, and no automation may issue or send a final
document without an authenticated human action.

## Decision

Extend the isolated `tvg_erasmus` app with an `ErasmusCase` linked one-to-one to
the existing CareerHub `Application`, native Horilla `Candidate` and immutable
selected `InterviewDecision`. The case owns proposed and confirmed dates,
university/programme and funding declarations, working arrangements,
department/supervisor, curriculum identity, outstanding requirements, an HR
approval snapshot and immutable workflow events. It does not replace or change
Candidate, Recruitment, Employee or Leave semantics.

The candidate confirms dates through CareerHub. CareerHub forwards only the
opaque case id, stable Person id and dates through a dedicated bearer-protected
internal endpoint. PeopleOps rechecks candidate ownership, legal employer,
application activity and overlapping confirmed arrangements. Candidate-facing
projections contain no native ids, staff identities, internal exception reasons
or authorization details.

Each legal employer has an explicit arrangement policy. The normal minimum is
three calendar months and is configurable by an authorized administrator. A
shorter candidate-confirmed arrangement remains unchanged and blocks HR
approval until a separate `DurationException` is requested with a written
reason and approved by a different authorized employee. The exception captures
the dates and policy version in force; it ceases to match if dates or policy
change.

HR approval is permitted only after candidate confirmation, university
confirmation, a funding/eligibility declaration, curriculum reference/version
and a valid duration or matching approved exception. Approval records the human
actor and a complete immutable snapshot. Approved arrangements cannot be edited
in place; a future formal amendment workflow must preserve the existing
agreement.

Acceptance templates are company-owned, plain-text and versioned. Legal entity,
address, registration and authorized-signatory fields are mandatory explicit
inputs. Templates are editable until an authorized employee approves them;
approved versions are immutable and later changes require a new version. No
application default invents legal or signature data.

An acceptance letter can be issued only from HR-approved arrangements and an
active approved template belonging to the same legal employer. The issuing
employee explicitly authorizes generation. Each issue gets an employer-policy
number, issue date, monotonically increasing case document version, rendered
text, private PDF, digest and immutable authorization metadata. Reissuing
creates a new history row; it never overwrites a prior document.

Sending is a second explicit permission-checked human action and is allowed only
for the latest issued version. It changes status and commits an idempotent
outbox event; this phase does not send real email. CareerHub can download only a
sent/received PDF belonging to its authenticated Person. Candidate receipt is
recorded explicitly, and only that received state exposes Learning Agreement
readiness.

## Alternatives considered

1. Add arrangement and document fields directly to Horilla Candidate or
   Recruitment. Rejected because one candidate can have multiple applications,
   it would change upstream semantics and it would create an avoidable merge
   surface.
2. Use the existing offer-letter fields as Erasmus acceptance. Rejected because
   those fields do not capture university/curriculum arrangements, exception
   authorization, immutable template inputs or document history.
3. Automatically adjust short dates to three months. Rejected because that
   silently changes an agreement and removes the required human exception
   process.
4. Render a letter from Company fields or a built-in signature. Rejected because
   the available data is not a verified legal-document source and missing
   values must fail closed.
5. Generate or send as soon as all fields are present. Rejected because field
   completeness is not human HR approval or authorization to issue/send a legal
   document.

## Consequences

- Horilla remains the recruitment and employee system of record; Phase 9 adds
  linked TVG-owned aggregates and no replacement core model.
- Acceptance PDFs use UUID-only paths in a private storage root and are streamed
  only after owner/object authorization with no-store security headers.
- The dedicated arrangement service credential must be distinct from catalogue,
  identity, application, verification and interview credentials and must not be
  exposed through the public proxy.
- Letter-number sequences are serialized through the legal-employer policy.
  Production correctness relies on PostgreSQL row locking; SQLite is used only
  by isolated tests.
- Outbox delivery, retries, dead-letter handling and recipient safeguards remain
  future `tvg_automation` responsibilities. Until then, “send” means authorized
  secure portal availability, not external mail delivery.
- A formal amendment/cancellation workflow is required before approved dates or
  legal documents can be superseded operationally; existing records remain
  immutable in this phase.
