# ADR-002: CareerHub Public Read Boundary

## Status

Accepted for the Phase 3 public CareerHub catalogue on 2026-10-25.

## Context

`career.thevipgroup.eu` must show current job data owned by Horilla without mounting Horilla administration, staff APIs, media or candidate records on the public process. Phase 2 placed public presentation metadata beside authoritative Recruitment records, but that does not authorize the public website to query HR tables directly.

## Options considered

| Option | Advantages | Costs / risks |
|---|---|---|
| Query Horilla models directly from public views | Least implementation work | Public runtime receives HR database access; accidental joins can expose private fields; conflicts with the approved deployment boundary |
| Export or hard-code job JSON into the frontend | Strong runtime separation | Stale duplicated truth; drafts and closure changes can remain visible; manual publication failures |
| Field-allow-listed PeopleOps read API plus a separate public process | Live source of truth, explicit contract, route and credential isolation | One internal network request; service credential rotation and availability must be operated |

## Decision

Use a separately deployed Django process for CareerHub and a narrow `v1` PeopleOps read API.

- PeopleOps runs `tvg_careers.peopleops_urls`, which prepends only `/internal/careerhub/v1/` to the normal private Horilla routes.
- CareerHub runs `horilla.settings.careerhub`, `horilla.careerhub_urls` and `horilla.careerhub_wsgi`. Its URL tree has no admin, employee, broad API, private media, internal integration or Horilla authentication routes.
- CareerHub has no HR model imports or database configuration. `PeopleOpsJobClient` performs authenticated HTTP GETs to the internal API.
- The API uses an independent bearer token, constant-time comparison, no-store responses and a configured `TVG_LEGAL_EMPLOYER_ID`. A missing employer id returns an empty catalogue instead of querying every tenant.
- nginx denies `/internal/careerhub/` on the externally proxied PeopleOps surface. The CareerHub container calls the web container over the private Compose network.
- The serializer is an explicit dictionary, not a model serializer. It omits model primary keys, managers, users, employees, candidates, histories, documents, notes, ratings and audit actors.
- Only active `JobOpening`, Venture, legal-employer and Recruitment rows with `is_published=True` and `closed=False` are eligible. Application acceptance is separately calculated from opening and closing dates.
- Future and expired but still published roles may remain informative, but their application CTA is disabled. Draft or Horilla-closed roles are absent and direct detail requests return 404.

## Public contract

The list endpoint accepts only venture, department, category, location, work mode, Erasmus+, keyword and application-status filters. Unknown query parameters are discarded.

Each job projection contains opaque public id/slug, title, public role copy, Venture, legal-employer display name, Department/JobPosition labels, category, public skill labels, location/work mode, Erasmus+ applicability, expected dates/duration, vacancies, application dates, derived public status and canonical path.

## Security and operational consequences

- CareerHub availability now depends on the internal PeopleOps read API. If it fails, the public process returns 503 and does not substitute stale or hard-coded roles.
- The initial bearer token is a long-lived service credential. Production must store and rotate it as a secret and keep the endpoint on the private network. A later service-auth iteration should add overlapping-key rotation or short-lived workload identity/mTLS.
- Public security headers use a per-request CSP nonce. The site contains no application JavaScript or analytics dependency in this phase.
- Candidate sign-in is a no-input, noindex handoff placeholder. No candidate credential, profile or submission capability is implied by this decision.

## Verification

Automated tests cover exact-token denial, disabled-boundary denial, tenant scoping, safe-field projection, draft/closed exclusion, direct-detail consistency, public private-route 404s, API outage behavior, CTA closure rules and responsive/accessibility markup.
