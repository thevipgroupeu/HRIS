# ADR-003: CareerHub Candidate Authentication

## Status

Accepted for the Phase 4 candidate-authentication increment on 2026-10-25.

## Context

Horilla's legacy candidate self-tracking backend treats a candidate email and
phone number (including a last-ten-digits comparison) as credentials and stores
a raw Candidate database id in a bespoke session. CareerHub needs a real public
account without making a Horilla Candidate, Employee or staff user into a public
authentication principal.

The Phase 2 `tvg_core.Person` is the stable employer-scoped identity, but it is
stored in PeopleOps and deliberately has no credentials. The Phase 3 public
CareerHub process has no HR database access. Authentication must preserve both
boundaries.

## Identity and threat model

The principal is an external applicant using a personal email address. It is
not a Horilla user, employee SSO identity, administrator or service account.
Relevant threats are credential stuffing, account/email enumeration, session
fixation, CSRF, reset/verification token theft or replay, duplicate accounts,
cross-account access, abuse of outbound email and accidental privilege or HR
database exposure.

## Decision

Use a dedicated database-backed Django session account in the public CareerHub
runtime.

- `tvg_careers.accounts.CandidateAccount` is the custom public user model. It
  derives from `AbstractBaseUser`, uses a normalized case-insensitive unique
  email and Django's supported password hashers, and has no staff, superuser,
  group or permission capability.
- CareerHub installs only Django auth/contenttypes/sessions/messages/staticfiles
  and the public account app. It does not install Horilla staff, Recruitment,
  Candidate, Employee or PeopleOps applications and has a separate database,
  cache, secret, cookie name and authentication backend.
- Signup never logs the applicant in. Email ownership must be verified before
  login. Verification links are signed, purpose-scoped and expire after 24
  hours. Password-reset links use Django's one-time password reset tokens and
  expire after one hour. Passwords are never emailed.
- Django rotates the session id at login. Logout is POST-only and flushes the
  session. A password reset changes the session authentication hash, invalidating
  existing authenticated sessions on their next request.
- Signup and recovery responses are deliberately generic. A repeated signup
  may resend verification to an existing unverified account, subject to both
  email and IP throttles, but never reveals whether a verified account exists.
- Authentication throttles use HMAC-digested email/IP keys. Production requires
  a shared Redis cache so limits work across Gunicorn workers; local development
  and tests use isolated memory caches and a local-memory email backend.
- The required checkbox records acknowledgement of a versioned privacy notice;
  it is not represented as GDPR consent. A separate optional talent-pool contact
  consent is recorded with its own timestamp and can be changed in the profile.

## Person integration

Each CandidateAccount generates a random proposed `person_public_id`. After the
email link is confirmed, CareerHub calls a private, authenticated PeopleOps
identity command. PeopleOps may create a Person with that exact id and email, or
confirm the same existing idempotent mapping.

PeopleOps does **not** automatically merge an account into a different Person
only because the email matches. That case returns a conflict and the account is
marked `review_required`. The verified applicant may sign in and maintain their
account profile, but future application/submission functions must require
`identity_status == linked`. Human-authorized identity reconciliation is a later
workflow.

No Candidate or Application row is created during signup, verification, login,
recovery or profile editing.

## Authorization and privacy controls

- Candidate pages resolve the account only from `request.user`; no public user
  id selects another account.
- Account routes are `noindex`, `no-store`, CSRF-protected and covered by the
  existing strict CSP. Redirect targets must be same-origin relative paths.
- Security-event records contain an event type and keyed hashes of network/user
  agent attributes, never passwords, reset/verification tokens or raw IPs.
- Email sending is synchronous but retryable by repeating the generic signup or
  recovery request. Development/test defaults cannot send external mail.
- The CareerHub URL tree still excludes Horilla admin and staff authentication.
  PeopleOps masks the legacy candidate login and self-tracking entry routes.

## Consequences

- CareerHub now needs a dedicated PostgreSQL database and shared Redis cache in
  production. The supplied Compose services are separate from PeopleOps data.
- The account migration is an explicit release step against only that dedicated
  database; it is not run automatically by the web process and must follow the
  approved staging/production procedure:

  ```console
  docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm careerhub python manage.py migrate --settings=horilla.settings.careerhub
  ```
- Availability of first-time verification depends on the private identity
  command. A temporary failure leaves the signed link retryable and does not
  partially activate the account.
- Staff SSO remains untouched. Future authentik work applies to staff and must
  not silently link or elevate public candidate accounts.
- Application submission and candidate-visible status were added in the next
  increment and enforce the linked Person invariant. See ADR-004.
