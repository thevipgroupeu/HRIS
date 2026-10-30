# Production release gate status — 30 October 2026

**Decision: NO-GO for production; eligible for an isolated staging rehearsal.**

This is the current closure record for the historical audit in
`docs/audit/2026-10-28/`. It does not rewrite that evidence and it does not
authorize deployment. The live server runs a different repository, so results
from this checkout cannot establish the behavior of either live hostname.

## Closed in this checkout

| Gate | Current evidence | Status |
| --- | --- | --- |
| Candidate and staff authentication separation | Dedicated CareerHub/PeopleHub settings, URL configurations, cookies and tests | Closed locally |
| Candidate/document object authorization | Employer-scoped candidate, document and media policies with negative-path tests | Closed locally |
| Internal self-registration | PeopleHub exposes login only; signup/bootstrap/legacy creation routes are absent and tested | Closed locally |
| Secure Django defaults | Production guards reject debug mode, wildcard hosts and weak secrets | Closed locally |
| Migration consistency | Both registries have no model drift; CareerHub migration `0006` reverses and reapplies in disposable tests | Closed locally |
| PostgreSQL and Redis compatibility | Exact image passed 44 public and 265 private tests on PostgreSQL 16.15 and authenticated Redis 7.4.11 | Closed locally |
| Container dependency findings | Trivy reports zero fixable HIGH/CRITICAL findings for the tested image digest | Closed locally |
| Host routing contract | Synthetic nginx checks cover both hosts, the unknown-host rejection and trusted-header boundary | Closed locally |
| Compose contract | Read-only checks cover runtime settings, health probes and service separation | Closed locally |

Tested image digest:
`sha256:0026b92a5d6bfacabebdada31cb48dcd4612d0a32ddad1e06c490bccae699cc3`.
This local image is evidence, not the approved production artifact. Rebuild from
the reviewed commit and verify the new digest before release.

## Required before production approval

| Gate | Required evidence | Blocking |
| --- | --- | --- |
| Repository reconciliation | Review and merge the intern-maintained server repository, commit the intended tree and run remote CI | Yes |
| Staging host acceptance | Candidate signup/sign-in and employee login through nginx and Cloudflare; internal signup must remain 404 | Yes |
| HR administration boundary | A protected HR entry point and role/object-permission acceptance without exposing the full native tree to ordinary staff | Yes |
| Upload malware protection | Approved quarantine/scanning service, failure behavior and operational monitoring | Yes |
| Backup and restore | Coordinated encrypted/off-site backups of both databases and all private files, followed by clean-host restore within approved RPO/RTO | Yes |
| Continuous lifecycle test | One fictional FluxOps lifecycle through After Mobility plus an independent FindMyStay application under the same identity | Yes |
| Email and automation | Test-mail sink results, approved templates/Reply-To, scheduler singleton/alerts and the first three authenticated Activepieces staging flows | Yes |
| Deprovisioning | Provider or authorized manual proof that mail, GitHub, VPN, SSO and application access are revoked | Yes |
| Privacy and retention | Approved retention schedule, DSR/deletion procedure and legal-record preservation controls | Yes |
| Accessibility and capacity | Mobile/accessibility acceptance and representative multi-user load results against agreed targets | Yes |
| Operations | Structured-log redaction, monitoring, alert ownership, incident response and human release approvals | Yes |

Recommended initial acceptance targets, pending business approval, are 100
applications/day, 25 concurrent public users, 10 concurrent staff users, p95
dynamic response time below 1 second at the origin, RPO at most 24 hours and RTO
at most 4 hours. These are planning assumptions, not demonstrated guarantees.

## Hostname acceptance criteria

- `https://career.thevipgroup.eu/candidate/create-account/` returns the external
  candidate registration form.
- `https://career.thevipgroup.eu/candidate/sign-in/` returns the candidate login.
- `https://people.thevipgroup.eu/login/` returns or is protected in front of the
  employee login.
- PeopleHub has no public registration endpoint. Attempts to use candidate,
  signup, bootstrap or legacy account-creation paths return 404 without changing
  the user count.
- Cloudflare Tunnel is transport only. Django permissions and object checks are
  enforced after the edge gate.

The currently observed CareerHub nginx 404 is an unresolved deployment/repository
routing issue. It is not a password or candidate-account problem, and changing
the database will not fix it.
