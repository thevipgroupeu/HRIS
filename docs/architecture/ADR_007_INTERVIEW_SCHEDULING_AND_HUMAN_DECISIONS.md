# ADR-007: Interview scheduling and human recruitment decisions

## Status

Accepted for Phase 8 implementation on 2026-10-26.

## Context

Horilla's `InterviewSchedule` is the internal recruitment record. It stores a Candidate, interviewers, a date/time, description and completion flag. It does not represent a candidate invitation, available slots, timezone-aware intervals, provider references, reschedule policy, structured feedback, decision-message approval or an immutable event history.

Phase 7 already enforces that a CareerHub application must be shortlisted and have every required verification approved before an `InterviewSchedule` can be created or a Candidate can enter an interview stage. CareerHub and PeopleOps are separate runtimes and databases, so a candidate command must cross the narrow internal API boundary without exposing Horilla administration or private feedback.

## Decision

Create an isolated `tvg_interviews` modular-monolith app in PeopleOps. It owns interview types, invitations, timezone-aware slots, bookings, provider references, policy counters, staff-only feedback, human decisions, immutable workflow events and a notification outbox. Each confirmed booking creates and links one native Horilla `InterviewSchedule`; each booking and decision moves the existing Horilla Candidate through configured native Stage records.

The domain service is the only supported command path for invitation, booking, rescheduling, completion, feedback, cancellation and decision recording. Invitation creation rechecks the Phase 7 eligibility policy. Booking also rechecks eligibility inside a transaction and locks the invitation/slot. Candidate and interviewer overlaps are rejected before the native schedule is created.

Calendar providers implement a small idempotent `book`, `reschedule` and `cancel` interface. Automated tests and local staging use a no-network simulated provider. The `zoho_bookings` and `self_hosted` keys fail closed until an approved adapter, credentials and operational runbook are configured. Provider implementations must use the supplied idempotency key and must never receive verification documents, feedback or decision rationale.

All persisted instants are timezone-aware. Europe/Warsaw is the company timezone and is used when projecting into Horilla's native date/time fields. Candidate responses retain an IANA timezone and show both local and company representations.

Candidates receive one self-service reschedule by default. The service enforces the configured minimum, initially 12 hours. A staff reschedule inside that window requires both the management permission and the explicit override permission; staff overrides are recorded in the immutable event stream.

Interview feedback is private to assigned interviewers and authorized recruitment managers. Candidate projections never include feedback, recommendations, internal reasons or reviewer identity. A decision is valid only after completion and is immutable. Rejection requires an active, human-approved template for that recruitment and an authenticated human decision maker. No AI or automation path may select, reject or hold a candidate.

## Alternatives considered

1. Add the missing fields directly to `recruitment.InterviewSchedule`. Rejected because it creates a large upstream merge surface and overloads a simple Horilla record with public workflow and provider concerns.
2. Store the workflow only in Zoho Bookings. Rejected because PeopleOps would lose authoritative authorization, audit, decision and provider-portability controls.
3. Let CareerHub call the calendar provider directly. Rejected because it would place provider credentials and HR scheduling policy in the public runtime and could bypass verification and object authorization.
4. Reserve every offered slot globally. Rejected for this increment because an offered slot is not a booking; the transactional booking service instead checks candidate/interviewer overlaps at confirmation. Provider-side temporary holds can be added behind the same interface later.

## Consequences

- Horilla remains the recruitment system of record and its tables are unchanged.
- `tvg_interviews` is an additional justified module boundary beyond the initial seven proposals because scheduling applies to every venture, not only Erasmus+ cases, and has its own provider/security lifecycle.
- Notification rows are outbox events only; Phase 8 does not send real email. Delivery, retry and dead-letter behavior remains owned by future `tvg_automation` work.
- The service prevents overlap at booking time and locks the shared Employee and Person resources in deterministic order, serializing bookings across separate invitations on PostgreSQL. Provider-side temporary holds may be added behind the same interface later.
- A separate bearer token is used for CareerHub interview commands. It must be distinct from catalogue, identity, application and verification tokens.
