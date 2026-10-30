# ADR-014: Reliable workflow automation boundary

**Status**: Accepted for the Phase 15 implementation slice  
**Date**: 2026-10-27  
**Related**: ADR-002, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009, ADR-010, ADR-012, ADR-013

## Context

PeopleOps already records application-confirmation and Erasmus verification
notification events in the same transactions as their authoritative Horilla
changes. Later workflow phases also need email, scheduling, signing, learning,
account and offboarding orchestration. Activepieces Community Edition is the
orchestrator, but it must not become a second HR database or receive a general
credential capable of changing arbitrary Horilla tables.

Delivery can fail after a database transaction commits, callbacks can be
duplicated or forged, and a worker can stop while a message is in flight. Human
decisions such as selection, rejection, document authorization and legal
document issue must remain explicit Horilla actions. Non-production execution
must not send real messages.

## Decision drivers

- Keep Horilla and the isolated `tvg_*` records authoritative.
- Preserve existing domain event identifiers and immutable audit history.
- Provide at-least-once delivery with end-to-end idempotency.
- Make failures, retries, cancellation and replay visible to authorized staff.
- Authenticate both outbound webhooks and inbound execution callbacks.
- Keep provider credentials environment-specific and out of the database,
  payload history, flow exports and Git.
- Enforce human-approval requirements before dispatch rather than in
  Activepieces.
- Limit callbacks to integration execution state; never accept generic model or
  field mutation instructions.

## Considered options

### Let Activepieces read and write the Horilla database

This is operationally simple but gives the orchestrator unrestricted access,
bypasses Django authorization and model validation, and makes audit attribution
unreliable. It was rejected.

### Send webhooks directly inside HTTP requests

This has no durable recovery when Activepieces is unavailable and can either
lose an event after commit or roll back a valid HR action because a downstream
service failed. It was rejected.

### Use a versioned transactional outbox and narrow callback API

This adds a small amount of state and a scheduler-owned dispatcher, but keeps
the business transaction independent of network availability and makes every
attempt reviewable. It also creates a stable boundary if Zoho, the signing
provider, authentik or Moodle changes later. This option was selected.

## Decision

Create the isolated `tvg_automation` app.

- A `LifecycleEvent` is registered in the same transaction as its source
  domain event. It stores an immutable, versioned, data-minimized payload,
  aggregate reference, company, workflow key and idempotency key.
- Delivery state is mutable only through the automation service. Every network
  attempt is appended to immutable `DeliveryAttempt` history. Exponential
  retries end in a reviewable failure state.
- The dedicated Horilla scheduler process claims due records with a lease and
  sends canonical JSON to one environment-configured Activepieces webhook.
  HMAC-SHA256 covers the timestamp and exact body. Each immutable event also
  carries a stable, source-derived delivery idempotency key.
- Activepieces receives a rendered message only after an immutable,
  company-scoped template version has been approved by an authorized employee.
  Recruitment messages use `recruitment@thevipgroup.eu` and retain an explicit
  Reply-To value. Provider secrets are read only from environment settings.
- Event definitions declare whether prior human approval is mandatory.
  Dispatch fails closed while approval evidence is absent. Activepieces cannot
  supply or override that approval.
- The callback endpoint accepts only allow-listed workflow execution states for
  a known event/workflow pair. It uses a separate HMAC secret, timestamp window,
  body hash and idempotency key. It cannot name a Django model, table, field or
  business command.
- Cancellation is allowed before delivery. Replay is permission-gated, records
  a reason and actor, and is limited to failed events; it does not alter the
  source business record or erase previous attempts.
- Production transport is disabled until its URL and independent signing
  secrets are configured. Tests use a synthetic transport and Django's
  local-memory mail backend; no test flow contacts Zoho or Activepieces.
- Initial support is limited to application acknowledgment, shortlisted
  verification request and verification document reminder. Remaining Phase 15
  events are catalogued with their approval boundary but require later producer
  integration.

## Consequences

### Positive

- HR transactions remain available when Activepieces or Zoho is down.
- Duplicate scheduler runs, webhook retries and callbacks are idempotent.
- Operators can see delivery attempts, terminal failures and execution status
  without exposing credentials or full candidate documents.
- Provider migration changes an adapter or flow, not Horilla's business models.
- Human approval remains enforceable even if an Activepieces flow is edited.

### Negative

- Delivery is at least once, so every Activepieces flow must honor the supplied
  idempotency key.
- Approved template versions and Activepieces environment configuration must be
  provisioned before messages can leave the outbox.
- The current scheduler provides bounded polling rather than immediate broker
  delivery.

### Risks and mitigations

- **Forged or replayed callback**: separate HMAC secret, constant-time compare,
  timestamp skew limit and unique callback idempotency record.
- **Worker death after claim**: leases expire and the scheduler safely reclaims
  the event with the same idempotency key.
- **Candidate-data leakage**: stored payloads use opaque identifiers; recipient
  and rendered content are resolved only for the signed delivery envelope and
  are not copied into attempt logs.
- **Unauthorized decision automation**: event metadata declares approval
  requirements and the dispatcher validates Horilla-side approval evidence.
- **Unsafe replay**: only terminal failures are replayable, with a dedicated
  permission, actor, reason and retained attempt history.
- **Accidental real test email**: the test settings retain the local-memory
  backend and the synthetic transport performs no network or SMTP call.
