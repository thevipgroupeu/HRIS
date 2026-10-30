# TrainingHub, Moodle and authentik integration foundation

This Phase 16 slice is deliberately provider-neutral and disabled in normal
development/production configuration. Synthetic tests make no network calls.

## Data ownership

- Horilla: active Employee, completed onboarding, supervisor, approved training
  requirement, deadline, authorization and progress projection.
- Moodle: course content, learning activities, grading and completion evidence.
- authentik: staff authentication session and provider identity.
- Activepieces: authenticated orchestration only; no Horilla or Moodle database
  credentials.
- CareerHub: independent applicant credentials and sessions. Candidate accounts
  are never accepted as PeopleHub staff identities.

## Programme matching

Approved programme versions cover these configured categories:

1. HR and People Operations
2. FluxOps engineering / AI / MLOps
3. FindMyStay software and product
4. FindMyStay marketing and growth
5. Sales and business development
6. Company policies and onboarding
7. IT and infrastructure

Matching uses legal employer, employee/intern audience and optional Venture and
Department. Course membership is frozen at programme approval. Change the
version instead of editing an agreement already assigned to staff.

## Activepieces/Moodle adapter contract

Implement an approved backend class with the interface in
`tvg_training.integrations.TrainingProvider`, then set:

```text
TVG_TRAINING_PROVIDER=activepieces
TVG_TRAINING_ACTIVEPIECES_BACKEND=approved.package.ActivepiecesMoodleProvider
```

The adapter receives only typed, bounded requests:

- ensure an account from staff reference, company reference, confirmed company
  email, first/last name and idempotency key;
- enroll an external user id into an external course id with deadline and
  idempotency key;
- return bounded progress for known external enrollment ids.

It must authenticate to Activepieces, use environment-owned credentials, honor
the supplied idempotency keys and return opaque references only. Activepieces
must call supported Moodle APIs and must not update Horilla tables. Errors must
not include tokens, provider payloads or personal data. Production and staging
reject the synthetic adapter.

## authentik standards plan

PeopleHub should be configured as an OpenID Connect relying party using
Authorization Code flow, exact HTTPS redirect URIs, state/nonce validation and
PKCE. Discover endpoints from authentik's issuer metadata. Keep a local,
restricted break-glass administrator until SSO recovery has been tested.

Moodle core supports custom OAuth 2/OpenID Connect issuers. Configure authentik
as the issuer and map only required Moodle fields. When Horilla/Activepieces
owns account provisioning, enable Moodle's setting that prevents account
creation during OAuth authentication. Do not rely on matching email to create,
merge or authorize an account.

Relevant upstream references:

- https://docs.goauthentik.io/add-secure-apps/providers/oauth2/
- https://docs.goauthentik.io/add-secure-apps/providers/oauth2/frontchannel_and_backchannel_logout/
- https://docs.moodle.org/39/en/OAuth_2_services
- https://docs.moodle.org/402/en/admin/setting/authsettingoauth2

## Role and session rules

- An authorized human first links an opaque authentik `sub` to an existing
  active Employee.
- An external group/role has no effect until its mapping is human-approved and
  the user already has that Group through `CompanyGroupAssignment` for the same
  company.
- Login evaluation never creates a User, Employee, Group or membership.
- Disabled Horilla users, inactive Employees, disabled provider accounts and
  disabled/revoked identity links are rejected.
- OIDC logout must end the local PeopleHub session. Administrative deactivation
  requires signed back-channel logout or a controlled synchronization command;
  the foundation's revocation service invalidates tracked Django sessions.

## Staging acceptance

Before enabling a real provider:

1. Use synthetic staff, company email addresses and courses.
2. Approve a programme version and explicitly assign it.
3. Confirm account and enrollment retries return the same external references.
4. Confirm PeopleHub shows only the employee's courses and progress.
5. Confirm an overdue mandatory item appears only for the correct supervisor.
6. Confirm unknown roles grant nothing, inactive users fail login and revocation
   terminates the tracked local session.
7. Export the reviewed Activepieces flow without connections or secrets.
