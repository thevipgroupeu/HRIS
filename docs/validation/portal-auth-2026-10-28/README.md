# Focused portal authentication validation

Continued and validated on **29 October 2026 (Europe/Warsaw)**. Folder name retains
the date this focused work began. This is not a production-readiness approval or a
replacement for `docs/audit/2026-10-28/`.

## Intended entry points

| Audience | Entry point | Registration policy |
|---|---|---|
| External candidate | `https://career.thevipgroup.eu/candidate/create-account/` | Self-registration with email verification |
| Returning candidate | `https://career.thevipgroup.eu/candidate/sign-in/` | Candidate credentials only |
| Internal employee/intern | `https://people.thevipgroup.eu/login/` | No self-registration; HR/IT-provisioned accounts only |

## Narrow fix

Added the missing `/change-password/` route required by Horilla's new-employee
password gate, using Django's existing-account password change implementation.
No schema change, account reset, migration against live data, deployment, real
email, or Cloudflare configuration change was performed. Existing unrelated work
was preserved.

The authentication and backend-security skills informed the least-privilege
route, native password/session reuse, CSRF checks, cross-account denial checks,
and separation between local test results and live acceptance evidence.

## Reproduce safely

From the repository root, using an installed isolated environment:

```powershell
.venv\Scripts\python.exe docs/validation/portal-auth-2026-10-28/run_tests.py internal
.venv\Scripts\python.exe docs/validation/portal-auth-2026-10-28/run_tests.py public
```

The runner removes inherited application environment variables and disables
`.env` loading. It selects the real PeopleHub or CareerHub settings module, then
overrides the database to disposable in-memory SQLite, cache to local memory,
media to a temporary directory, mail to local memory and hashing to a test-only
fast hasher. Candidate identity provisioning is mocked in the flow tests. Raw
logs and token-bearing verification URLs are not saved; only sanitized summaries
are written to `results-internal.json` and `results-public.json`.

Limitations: this run used Python **3.13.14**, not the required production/CI
Python 3.12; private migrations are disabled for these focused authentication
tests. Public migrations are enabled. These runs do not demonstrate PostgreSQL,
Redis, production password-hashing capacity, actual SMTP delivery, a live identity
API, browser accessibility, Cloudflare policy acceptance, or deployment parity.
The internal tests exercise the default password-only configuration, explicitly
disabling Horilla's optional email-based two-factor setting in the new test class.

## Coverage

Latest isolated results (29 October 2026, Warsaw):

| Suite/check | Result |
|---|---|
| PeopleHub, including staff login and password-change boundary | 20 tests passed |
| CareerHub candidate accounts, including continuous CSRF-enforced journey | 40 tests passed |
| Django checks invoked by both test runners | No issues reported |
| Ruff, Black check, isort check for the five affected Python files | Passed |

The initial added HTTPS test omitted the browser's Origin/Referer context and
correctly received a CSRF 403; it now sends the same-origin HTTPS Origin header.
The missing-employee session test was corrected to expect native middleware's
logout and login redirect, not a view-level 403. It asserts that the session is
unauthenticated and credentials remain unchanged. No authorization assertions
were replaced with successful access expectations.

- Internal employee login, session rotation, inactive/disabled/non-employee denial.
- GET/POST probes of signup, candidate signup, legacy registration, initial setup
  and onboarding account-creation routes: no accounts created.
- Candidate/staff cookie separation, wrong-host rejection, safe login redirects.
- CSRF protection for login/logout/password change; POST-only logout.
- First-login redirect, invalid current/weak/mismatched new passwords, successful
  password change, first-login flag handling, other-session invalidation.
- Active employee authorization on password change, including stale sessions.
- A supplied foreign-company user ID cannot change another employee's password.
- Continuous synthetic candidate signup, email-link confirmation, login, profile
  and logout with CSRF checks and HTTPS Origin headers enabled.
- Authenticated candidates cannot access staff routes or gain staff permissions.
- Existing public tests cover duplicate registration, recovery, token expiry,
  rate limiting, profile isolation, migration compatibility and application flows.

## Read-only live observations

Unauthenticated GET checks on 29 October 2026, without following redirects,
submitting credentials, creating accounts or bypassing edge controls:

| URL path | Observed response from this environment |
|---|---|
| PeopleHub `/login/` | HTTP 302 to a Cloudflare Access login host |
| CareerHub `/candidate/create-account/` | HTTP 403, `Attention Required! \| Cloudflare` |
| CareerHub `/candidate/sign-in/` | HTTP 403, `Attention Required! \| Cloudflare` |

These observations do not establish that all browsers are blocked, but they also
do not establish working public registration. Full redirect URLs and tokens were
not retained. Internal Access redirection does not verify the subsequent employee
login or authorization policy.

## Required follow-up before launch

1. Inspect Cloudflare security events for the public requests and validate the
   intended public policy on `career.thevipgroup.eu`. Keep `people.thevipgroup.eu`
   private. Do not disable all WAF protections or open the native HR routes.
2. Validate signup/verification/login in an ordinary browser through the exact
   deployment, using a synthetic account and a test mailbox under explicit
   staging authorization. Verify SMTP, identity API and correct host-only cookies.
3. Resolve the inherited optional MFA integration: `TwoFactorAuthMiddleware`
   redirects to `/two-factor`, absent from the PeopleHub URL tree, when enabled
   with a configured mail backend. The middleware also allows requests through
   when the backend is absent or errors. Do not claim MFA enforcement or enable
   this configuration for launch without fail-closed, end-to-end tests. Cloudflare
   Access may provide an additional approved factor, but its policy was not audited
   by this change and is not a substitute for Django object authorization.
4. Review inherited first-password middleware's HTMX-header exemption:
   `HX-Request` with `HX-Current-URL` pointing to an excluded path bypasses its
   first-password-change redirect. Header contents are not proof of a trusted page.
   The new view preserves the gate on normal requests but does not fix that upstream
   exception; enforce the intended restriction independently of client headers.
5. Run Python 3.12/PostgreSQL 16/Redis 7 CI, reviewed migrations and all security,
   recovery, retention and operational release gates in the main audit. The broader
   audit findings are not marked resolved by this focused work.

**Release decision remains NO-GO:** isolated tests are necessary but insufficient
for production readiness, and public access has not been demonstrated.
