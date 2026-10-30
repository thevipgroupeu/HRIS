# Login and production-hardening follow-up — 29–30 October 2026

**Release status: not yet approved for production. Nothing deployed.**

The operator confirmed that the live server currently runs another repository
maintained by an intern. This checkout is the planned replacement. Therefore
local test results do not establish that the live login/signup pages work.
The live candidate entry pages returned nginx 404 during read-only inspection;
PeopleHub reached Cloudflare Access, not the origin login form. No live accounts,
mail, migrations, applications or files were created/changed.

## Intended entry points

| Audience | URL | Registration policy |
| --- | --- | --- |
| Candidate signup | https://career.thevipgroup.eu/candidate/create-account/ | Public self-registration, verification required |
| Candidate login | https://career.thevipgroup.eu/candidate/sign-in/ | Separate candidate identity/database/cookies |
| Employee/intern login | https://people.thevipgroup.eu/login/ | Existing active Employee account only; **no public signup** |

These are verified application routes in this checkout, not a claim the current
server has been updated. Do not point either hostname at the full native Horilla
URL tree to bypass a 404. Internal `/signup/`, candidate registration, bootstrap,
admin and legacy account-creation routes are intentionally absent in PeopleHub.

## Implemented fixes

- Moved required TVG settings from Docker-excluded `local_settings.py` into shipped
  `horilla/settings/tvg.py`. The test runner deliberately excludes local overrides,
  just as release images do. Private app/security registration now fails visibly
  if required configuration cannot import.
- Production startup no longer migrates databases, clears static files or
  automatically replaces placeholder secrets. Explicit reviewed release jobs
  own those tasks. Build/Git excludes all default private-media directories.
- Container health checks now supply the exact runtime host/HTTPS scheme and
  reject redirects, avoiding false health or localhost host-validation failures.
- Candidate bridge status/withdrawal explicitly scope Person, opening venture,
  native recruitment and Candidate to the configured employer. Missing employer
  fails closed. Withdrawal locks its application row and remains idempotent.
- Native Candidate and CandidateDocument GET detail routes now use their list
  authorization scopes. TVG scopes require an active employee, model permission
  and an authorized company, including company-specific group permissions.
- Native media uses a narrow TVG object-authorization hook rather than merely a
  logged-in session. CV/profile and candidate documents require their respective
  permissions and company access. Own employee photo and explicitly assigned/
  published company policy attachments have specific policies. Unknown/unowned
  files are denied. Candidate photos are no longer anonymous public assets.
  Allowed private downloads have `private, no-store` caching.
- Production settings reject DEBUG, short/trivial secrets and wildcard hosts.
  PeopleHub validates its own final host override and uses host-only cookies.
- Added the missing CareerHub security-event choices migration `0006`.
- FakeCleanScanner refuses production, staging and unspecified environments;
  this does **not** implement or approve a real malware scanning service.
- Raised dependency pins to Django 5.2.18 minimum, DRF 3.18.3, urllib3 2.8.0
  and msgpack 1.2.3. The final runtime resolves msgpack 1.2.4. Package installers
  and setuptools are removed from the production stage after dependencies are
  built, eliminating pip's vulnerable vendored libraries from runtime.
- nginx explicitly separates hostnames, rejects unknown hosts and provides a
  dedicated trusted-connector listener. Ordinary HTTP ignores forwarded HTTPS/
  client-IP headers. Optional tunnel overlay binds the trusted listener only to
  host loopback. It must not be exposed to untrusted clients or containers.
- nginx/Gunicorn access formats omit URL paths, queries, referrers and personal
  identifiers. Error/application/Cloudflare log redaction remains a release gate.
- Added independent public/private schema and synthetic test CI jobs. Added
  PostgreSQL 16/Redis 7 boundary CI and synthetic nginx checks. Private row
  locks now target the primary TVG row explicitly, avoiding PostgreSQL's
  prohibition on locking nullable outer-join relations while preserving the
  intended serialization boundary.
- Download-response tests no longer emit Django's global `request_finished`
  signal while a `TestCase` transaction is open. SQLite hid that harness
  problem; both public and private suites now pass on PostgreSQL.

Authentication-pattern guidance was applied to separate candidate/staff sessions,
enforce least privilege and test both allowed and denied access.

## Evidence and reproduction

Every Django check runs with `.env` loading disabled, inherited integration
credentials removed, in-memory SQLite, temporary private storage and local-memory
mail. Only summary evidence is retained. Historical audit evidence in
`docs/audit/2026-10-28/` is not rewritten.

- `evidence-public.json`: candidate suite, including registration, verification,
  sign-in, recovery, CSRF, document authorization and external hostname routes.
- `evidence-private.json`: complete TVG extension suite.
- `evidence-focused.json`: new authorization/configuration and employee login tests.
- `evidence-native.json`: selected native authorization suites on DRF 3.18.3.
- `evidence-public-schema.json`: no drift; public migration graph applied, `0006`
  reversed to `0005` and reapplied in disposable SQLite.
- `evidence-private-schema.json`: no drift; Django and tenancy checks pass.
- `evidence-proxy.json`: seven synthetic nginx routing/header/log assertions.
  This is not an actual Cloudflare-to-Django acceptance test.
- `evidence-compose.json`: eight read-only Compose contracts using synthetic
  variables and with service `.env` resolution disabled; no service is started.
- `evidence-image.json`: the locally built image digest runs as UID 1000 on
  Python 3.12.16 with the reviewed dependency versions and required release
  files; local settings, environment secrets and bundled private media are
  absent. The probe is read-only and network-disabled.
- `evidence-vulnerabilities.json`: Trivy 0.58.1 reports zero **fixable** HIGH or
  CRITICAL findings for the same image digest. This is not an approval of
  unfixed advisories, licenses, secrets or application behavior.
- `evidence-postgres-redis.json`: the exact final image passes 44 isolated
  CareerHub tests and 265 private TVG tests on PostgreSQL 16.15 with
  authenticated Redis 7.4.11. All data and adapters are synthetic.

The exact final image was also used for the isolated suites: 44 public tests and
265 private TVG tests pass on both SQLite and PostgreSQL 16; the PostgreSQL runs
use authenticated Redis 7. Both no-drift checks and the public `0006` forward/
backward/forward rehearsal pass on Python 3.12.16. These local backend tests do
not replace multi-worker concurrency or deployed staging acceptance.

Run with Python 3.12 and reviewed requirements in an isolated development venv:

```text
python scripts/check_tvg_release.py public
python scripts/check_tvg_release.py public --schema
python scripts/check_tvg_release.py private
python scripts/check_tvg_release.py private --schema
python scripts/check_tvg_proxy.py
python scripts/check_tvg_compose.py
python scripts/check_tvg_image.py <image-or-digest>
python scripts/check_tvg_vulnerabilities.py <image-or-digest>
```

The host interpreter used for some earlier checks was Python 3.13.14; the final
image checks and extension suites now run on the required Python **3.12.16**.
The new CI has not been executed remotely here. The local PostgreSQL/Redis tests
do not prove multi-worker races, deployed rate limiting or failover. Do not
interpret these suites as the continuous, two-runtime lifecycle acceptance test.

## Remaining launch blockers

1. Reconcile this working tree with the intern's server repository; review all
   existing uncommitted/untracked customization, commit the intended release,
   run the new CI remotely and promote an immutable staging artifact. Local
   Python 3.12/PostgreSQL 16/Redis 7 gates pass, but full-HR write endpoints and
   other native modules still need their separate permission review.
2. Validate the actual Cloudflare/origin topology, firewall, proxy trust, client
   IP/rate limiting, TLS, cookies and CSRF. Choose a protected HR administration
   entry point; do not expose native HR modules to ordinary employees to solve it.
3. Approve and connect a real quarantine/scanning service for all relevant upload
   classes. Do not enable the fake scanner to get production documents unstuck.
4. Add reviewed owner policies for any additional native media classes needed
   in rollout (for example asset or leave attachments). They currently fail
   closed; disabling the hook is not an acceptable workaround.
5. Promote an image rebuilt from a reviewed committed tree, generate/review its
   SBOM and secret/license scans, and give every remaining no-fix advisory
   (including pdfkit) a scoped mitigation, owner and expiry. The local readiness
   image has zero fixable HIGH/CRITICAL findings but is not a release artifact.
6. Prove coordinated encrypted/off-site backups and clean-host restore of both
   databases and every private media root within approved RPO/RTO. Preserve the
   prior synthetic restore evidence but do not substitute it for operational recovery.
7. Complete the continuous fictional FluxOps lifecycle and independent FindMyStay
   application on the same identity through both runtimes, with fake external
   effects, approved synthetic templates and real human authorization boundaries.
8. Complete SMTP/Reply-To/templates, scheduler singleton/alerts, Activepieces
   staging flows, provider/manual deactivation proof, privacy/retention/DSR,
   accessibility/mobile and representative load acceptance from the audit checklist.

See [SERVER_HANDOFF.md](SERVER_HANDOFF.md) and the original
[release checklist](../../audit/2026-10-28/RELEASE_CHECKLIST.md).
The confirmed Linux/Docker/Tunnel rollout plan is in
[LINUX_LOCAL_SERVER.md](LINUX_LOCAL_SERVER.md).

References: [DRF release notes](https://www.django-rest-framework.org/community/release-notes/),
[Cloudflare ingress configuration](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/).
