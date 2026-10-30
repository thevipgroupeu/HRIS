# PeopleHub login and first-password-change boundary

Date: 29 October 2026. Scope: restricted PeopleHub authentication only.

## Public and private identities

- `career.thevipgroup.eu` uses `horilla.settings.careerhub`, its independent
  candidate account model/database, and CareerHub cookies. Candidate registration
  is `/candidate/create-account/`; candidate login is `/candidate/sign-in/`.
- `people.thevipgroup.eu` uses `horilla.settings.peoplehub`, native Horilla users
  linked to active employees, and PeopleHub cookies. Staff login is `/login/`.
- PeopleHub does not expose self-registration, initial database setup, native
  onboarding account-creation routes, or candidate registration. HR/IT provision
  staff accounts through an authorized private process. An applicant account is
  not an employee account and must never confer staff permissions.

## Existing-account password changes

Horilla's `ForcePasswordChangeMiddleware` redirects new employees to the
unnamespaced route `change-password`. The restricted PeopleHub URL tree previously
omitted that route, causing first-login navigation to fail.

The extension now supplies `/change-password/` through
`tvg_peoplehub.account_views.PeopleHubPasswordChangeView`, reusing Django's
`PasswordChangeView` and Horilla's existing user model. It does not expose a signup
or identity-based reset endpoint and adds no tables or migrations.

The view requires an authenticated user with an active employee/company link,
verifies their current password, applies existing password validators, requires
CSRF protection, and never accepts a target user from request data. After a valid
change, the native `is_new_employee` flag is cleared within the same database
transaction. Django rotates the current session key and invalidates other sessions
when they are next used. Invalid submissions preserve both password and flag.
Responses are non-cacheable. Templates use the restricted portal context rather
than loading the full HR navigation.

The login page explicitly states that PeopleHub has no public signup. The full
native HR URL tree remains excluded; adding password change must not be used as
a reason to expose administrative routes on this hostname.

## Validation and limitations

See `docs/validation/portal-auth-2026-10-28/README.md` for isolated runtime tests,
live edge observations and remaining release gates. This change does not certify
the entire login deployment or resolve inherited MFA middleware behavior.
