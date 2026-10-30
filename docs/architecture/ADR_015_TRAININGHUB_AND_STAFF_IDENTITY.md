# ADR-015: TrainingHub and staff identity integration

**Status**: Accepted for the Phase 16 foundation  
**Date**: 2026-10-27  
**Related**: ADR-003, ADR-010, ADR-011, ADR-014

## Context

Horilla owns staff, onboarding approval, supervisors and authorization. Moodle
owns course content, activities and completion evidence. authentik is the future
staff identity provider, while Activepieces may orchestrate provisioning and
synchronization. The systems must not silently create parallel HR identities,
grant roles from untrusted claims, or turn Moodle into an HR system of record.

The existing PeopleHub learning model is deliberately presentation-only. It can
show a course, deadline and progress but cannot define reusable programmes,
record approved curriculum versions, reconcile Moodle identities/enrollments or
revoke SSO sessions.

## Decision drivers

- Keep Horilla authoritative for employment, onboarding, training requirement
  and role authorization.
- Keep Moodle authoritative for course delivery and detailed learning activity.
- Keep applicant CareerHub credentials isolated from all staff identities.
- Permit provider replacement without changing Employee or onboarding models.
- Require human approval before assigning/provisioning training.
- Never treat an authentik claim as permission to create a user or grant a
  Horilla role.
- Store opaque external references and progress summaries, not tokens, Moodle
  activity data or course content.

## Considered options

### Rebuild training inside Horilla

Rejected. It would duplicate Moodle course delivery, quizzes, completion logic
and learning records while increasing operational and privacy scope.

### Give Activepieces direct Horilla and Moodle database access

Rejected. This bypasses validation and object authorization, makes attribution
unreliable and turns an orchestration compromise into unrestricted HR access.

### Use approved programme records plus narrow provider adapters

Selected. Horilla stores the requirement and opaque mapping; a provider adapter
can later call authenticated Activepieces flows, which in turn call supported
Moodle APIs. Results return through allow-listed service methods. Mock and
disabled adapters make tests deterministic and production fail closed.

## Decision

- Add isolated `tvg_training` models for reusable versioned programmes,
  programme-course membership, employee assignments, Moodle account/enrollment
  mappings, synchronization attempts, overdue notifications and audit events.
- Programme versions are editable until an authorized employee approves them.
  The curriculum and scope of an approved version are immutable. New content
  requires a new version; existing assignments do not silently change.
- Programme selection is deterministic: legal employer, active approved
  version, employee/intern audience, optional Venture and optional Department.
  An intern must have a completed `InternOnboarding`; all staff must have active
  Employee and Horilla user records.
- Human assignment authorization is retained before a provider can provision or
  link a Moodle account. Provider calls carry stable idempotency keys and the
  minimum company-email/staff/course fields required by the contract.
- Moodle remains authoritative for progress and completion. Horilla stores a
  bounded status/progress/deadline projection and updates the existing
  `PeopleHubLearningAssignment` for the owning employee only.
- Mandatory overdue training creates an idempotent supervisor notification in
  Horilla. External notification delivery can later use the Phase 15 outbox.
- PeopleHub SSO will use OpenID Connect Authorization Code flow with exact
  redirect URIs, state and nonce validation and PKCE where supported. No token
  is stored in the new integration tables.
- Moodle may use its core OAuth 2/OpenID Connect issuer support with authentik.
  Automatic Moodle account creation must remain disabled when Horilla-driven
  provisioning is selected, preventing email-only identity creation/linking.
- An authentik subject must be explicitly linked to an active Employee by an
  authorized human. Approved external-role mappings are advisory: a role is
  effective only when the Horilla user already has the matching
  company-scoped group assignment. Evaluation never mutates membership.
- The foundation records hashes of staff session identifiers. Revocation
  invalidates tracked Django sessions and increments the identity session
  version. A later OIDC implementation must validate signed front-/back-channel
  logout tokens before invoking the same revocation service.
- CareerHub retains its own credential database, cookies, routes and Person
  provisioning boundary. It does not import staff links or accept authentik
  staff sessions.

## Consequences

### Positive

- Programmes are reusable and auditable without copying Moodle functionality.
- Provider changes affect adapters and mappings rather than Employee records.
- One authorized operator can assign and monitor normal training by exception,
  while failed synchronization and overdue mandatory items remain visible.
- SSO cannot silently escalate privileges or reactivate a disabled employee.

### Negative

- Moodle/Activepieces production provisioning remains disabled until an
  approved API contract and environment credentials are supplied.
- Progress is eventually consistent and is intentionally less detailed than
  Moodle's own activity records.
- Each target application still needs independent logout and session-revocation
  validation; deploying authentik alone does not provide that behavior.

### Risks and mitigations

- **Duplicate enrollment**: stable per-employee/programme/course idempotency key
  and unique mappings.
- **Stale role claim**: intersection with existing company-scoped Horilla group
  assignments; no group mutation during login.
- **Disabled staff access**: Employee, Horilla user, identity-link and provider
  account state all fail closed.
- **Session theft/reuse**: store hashes only, revoke tracked sessions, require
  standard OIDC state/nonce/logout-token validation in the real adapter.
- **Cross-company disclosure**: company-derived managers, scope checks and
  employee-only PeopleHub projection.
