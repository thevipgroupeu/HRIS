# Implementation Roadmap

This roadmap is intentionally sequenced around security and data ownership. It does not authorize product implementation, production migrations, live email, live status changes or destructive environment work.

## Phase 0 — Reproducible baseline and decisions

1. Create an isolated Python 3.12/PostgreSQL 16/Redis staging environment with synthetic data, fake storage and a sink/local-memory email backend.
2. Run and record all current CI checks before changing behavior. Add a test-environment assertion that refuses known production hosts/databases and non-allow-listed email recipients.
3. Configure a read-only authoritative Horilla upstream remote and fetch release tags; document the fork base/update procedure.
4. Write architecture decisions for candidate auth, CareerHub database/API boundary, GDPR/retention, document scanning, verification override, authentik linking and event contracts.
5. Produce a data classification/flow inventory and a DPIA/security review for candidate identity and university documents.

**Exit:** repeatable green baseline, no production connection path, approved boundary/data-retention decisions.

## Phase 1 — Security foundation

1. Split CareerHub and PeopleHub settings, URLconfs, WSGI/ASGI entrypoints, cookie names, keys, database roles and Compose services. Add host-denial tests proving admin, staff, API and media routes are absent from CareerHub.
2. Remove direct public binding of the PeopleOps web port; expose only the approved reverse proxy/tunnel path.
3. Fix current authorization gaps before adding consumers:
   - require published/active recruitment in public detail;
   - replace recruitment API detail `IsAuthenticated`-only reads with explicit view/object policy;
   - use allow-listed integration serializers rather than `fields = "__all__"`;
   - authorize media through its owner object and private storage delivery;
   - retire the email+phone candidate login and harden/replace onboarding bearer tokens.
4. Extend immutable audit coverage to candidate documents, interviews, verification decisions, overrides, application exports and integration commands.

**Tests:** host/routing isolation, cross-tenant and same-tenant privilege negatives, IDOR/file-path negatives, token expiry/replay, session/cookie isolation, unpublished-job enumeration.

**Exit:** a public process cannot route to or read private HR resources even if proxy configuration is wrong.

## Phase 2 — TVG module skeleton and stable contracts

**Progress (2026-10-26):** the company-structure/CareerHub-domain slice is implemented in `tvg_core` and `tvg_careers`: Venture, Person, JobOpening and Application links, migrations, internal admin forms, state/link permission services, tests and an explicit synthetic demo command. Subsequent increments added the versioned public read contract, public routes, verified candidate accounts, reusable profiles/documents, immutable multi-application submissions and native-survey questionnaire versioning. The remaining TVG modules are still pending.

1. Add `tvg_core`, `tvg_careers`, `tvg_erasmus`, `tvg_documents`, `tvg_reports`, `tvg_automation` and `tvg_integrations` with migrations and focused test packages.
2. Introduce business-area configuration and opaque public ids.
3. Define the minimal CareerHub↔PeopleOps API, versioned event envelope, service authentication, idempotency and error model.
4. Add contract tests and schema documentation. Keep all external adapters fake.

**Exit:** empty/baseline apps install cleanly, migration drift is zero, boundaries are enforced by imports and contracts.

## Phase 3 — CareerHub accounts and applications

**Progress (2026-10-26):** complete. The separately deployed public CareerHub, field-allow-listed PeopleOps job API, requested information pages, listings/detail pages, filters, SEO metadata and responsive design are implemented. Candidate accounts use a dedicated credential database, verified signup, secure login/recovery/logout, privacy acknowledgement, Redis-backed throttling and narrow Person provisioning. The profile/application increments add reusable candidate details, private versioned PDF/DOCX files, application drafts, immutable questionnaire and submission snapshots, an idempotent Horilla Candidate/Application command, candidate-safe dashboard projections and withdrawal.

1. **Complete:** verified candidate account, password/reset, versioned notice acknowledgement, optional talent-pool consent, rate limiting and security audit events.
2. **Complete:** reusable candidate profile and CV/cover-letter versions.
3. **Complete:** public published-job questionnaire projection, draft application and immutable submission snapshot.
4. **Complete:** private idempotent command creating one Horilla Candidate application and returning the opaque application id.
5. **Complete:** multi-application dashboard/status projection without internal notes, ratings or other applicants.
6. **Complete:** native Horilla questionnaire assignment with immutable versions, server-side required/choice validation, safe source/UTM tracking and an idempotent submission confirmation event.

**Tests:** duplicate/retried submission, one identity/multiple jobs, profile change after submission, account recovery, consent version, enumeration resistance, upload limits/content, cross-account access.

**Exit:** CareerHub can submit and track synthetic applications without direct HR table access or real email.

## Phase 4 — Erasmus verification and interview gate

**Progress (2026-10-26):** the pre-interview verification slice is implemented.
Human screening, mandatory checklist snapshots, private versioned evidence,
candidate upload, HR review, candidate-safe notifications, retention metadata
and central schedule/stage eligibility enforcement live in `tvg_erasmus`.
Production scanner approval, notification delivery and an approved scheduled
retention job remain release prerequisites; the default scanner fails closed.

1. Add versioned university/program requirement templates and per-application requirement instances. Treat labels such as NA10 as configuration, never code.
2. Link evidence to existing candidate documents and store verification actor, timestamp, decision, reason and superseded versions.
3. Implement the central eligibility/interview policy. An override command is intentionally absent until an explicit policy, permission and audit design is approved.
4. Route HTML, API and automation interview/stage commands through that policy in an atomic transaction.

**Tests:** all decision transitions, stale/superseded evidence, missing/expired requirements, concurrency, override permission/reason, every interview entry point, no AI decision path.

**Exit:** it is impossible to schedule/advance to interview before approval except through an audited authorized override.

### Delivered Phase 8 increment — interview scheduling and decisions (2026-10-26)

The verified-candidate interview slice is implemented in `tvg_interviews`.
It adds configurable interview types, assigned interviewers, timezone-aware
candidate-selectable slots, one candidate reschedule with a 12-hour rule,
authorized HR override, booking conflict checks, a simulated calendar adapter,
native `InterviewSchedule`/Stage integration, private structured feedback,
human Selected/Rejected/On Hold decisions, approved rejection templates and
data-minimized notification events. Real provider credentials and real email
delivery remain disabled and are release prerequisites under the integration
phase.

### Delivered Phase 9 increment — selected-candidate arrangements and acceptance (2026-10-26)

The formal Erasmus+ acceptance slice is implemented in `tvg_erasmus`. It adds
one case per human-selected application, candidate-confirmed dates, university,
funding/eligibility, curriculum and supervision arrangements, a configurable
three-calendar-month policy, separately authorized short-duration exceptions,
immutable HR approval snapshots, approved company-authored template versions,
private versioned PDFs, letter numbering, explicit issue/send authorization and
candidate receipt tracking. Sending creates an outbox record only; no real mail
is enabled. Receipt makes the candidate ready for the future Learning Agreement
stage without automatically creating or signing that agreement.

### Delivered Phase 10 increment — Erasmus+ documentation lifecycle (2026-10-27)

The accepted candidate can now submit a university Learning Agreement through
CareerHub. PeopleOps provides permission-checked review, revision, approval,
version-bound signature and signed-return transitions, then materializes the
remaining approved university/company requirements. Approved private template
files can be copied only as drafts; no legal fields or signatures are invented.
The slice also supports immutable document history, university/programme scope,
required and optional before/during/after phases, Learning Agreement amendments,
manual signed-upload fallback, controlled downloads, a disabled self-hosted
signing adapter and a deliberately rule-free disabled NA10 boundary. A signed
Learning Agreement exposes the minimal Kraków accommodation-assistance question.

### Delivered Phase 11 increment — THEVIPGROUP intern onboarding (2026-10-27)

The accepted, date-confirmed candidate with complete mandatory pre-start
documentation can now enter an HR-approved `tvg_onboarding` workflow. It reuses
native Horilla onboarding stages/tasks, converts exactly once into Employee while
preserving Person/Application/Candidate history, generates separate configurable
intern and employee IDs, schedules welcome/arrival records, supports fail-closed
identity adapters with manual IT fallback, applies human-approved company-scoped
department/venture access, and allocates serial-bearing equipment through Horilla
Assets. Approved editable templates produce private versioned asset acknowledgment
PDFs. The focused synthetic path ends with the native candidate, employee,
onboarding and asset records linked and the intern marked onboarded; real mailbox
and email delivery remain disabled.

### Delivered Phase 12 increment — restricted PeopleHub portal (2026-10-27)

`people.thevipgroup.eu` now has a separate process, root URLconf and cookie names
that expose no native HR administration routes. A unified intern/employee
dashboard projects native profile, policy, attendance, leave, assets, project,
performance, learning and helpdesk data. Native leave requests and helpdesk
tickets remain the records of truth. Directory output has a fixed safe-field
allowlist; employee, direct-manager, HR, IT and management navigation is derived
from native relationships and permissions with company/object checks. Approved
request routing and minimal missing Venture/learning links live in
`tvg_peoplehub`. Local authentication remains active while an authentik adapter
boundary is disabled by default. The synthetic exit path proves an intern can use
the dashboard while HR administration returns 404.

### Delivered Phase 13 increment — internship reporting and performance (2026-10-27)

`tvg_reporting` now provides PeopleHub weekly reports and monthly PDF/PPTX
presentations linked to native Employee, Project and Task records. Draft,
submission, revision and approval transitions create immutable content and review
versions. Company-configurable deadlines feed deduplicated Horilla notification
records dispatched only by the single scheduler. Versioned, human-approved
rubrics separate report-writing quality from internship performance; management
sees scoped status summaries. An optional provenance-bearing AI adapter can
summarize and identify missing evidence but is disabled by default and cannot
make performance, disciplinary or recognition decisions. The synthetic exit path
submits both artifacts and returns supervisor feedback to the test intern.

### Delivered Phase 14 increment — Erasmus+ completion and offboarding (2026-10-27)

`tvg_offboarding` now links one internship-completion case to the Phase 11
onboarding record and Horilla's native `OffboardingEmployee`. Configurable
lead-time automation opens Upcoming cases and queues deduplicated HR/supervisor
notifications. The completion gate consumes approved Phase 13 final reporting,
Phase 10 university-specific After Internship documents and native Horilla asset
returns. Approved company template versions produce private, versioned completion,
asset-return and optional reference PDFs; separately authorized humans record each
required signature. Provider-neutral access-revocation tasks fail closed to a
tracked manual IT action when no approved API exists and require non-secret proof
or an explicit authorized exception. Versioned retention policies schedule human
review and never automatically delete historical recruitment, internship or legal
records. The synthetic exit path returns the asset, completes After Mobility,
revokes all configured access, issues the certificate, confirms administrative
closure and archives the native offboarding record.

## Phase 5 — Onboarding, employee and intern operations

1. Connect approved Candidate to existing onboarding stages/tasks and Employee conversion.
2. Replace unsafe onboarding token/state mechanics with expiring single-use state stored durably.
3. Add business area, internship assignment and training mappings without duplicating Employee.
4. Configure/reuse leave, attendance and assets; add only confirmed acknowledgement records.
5. Implement weekly/monthly intern report submission/review and integrate existing report subscriptions.

**Tests:** conversion idempotency, token expiry/replay, no duplicate Employee, manager/object permissions, leave/attendance tenant isolation, asset lifecycle, report deadlines/reviewer access.

**Exit:** a synthetic approved intern can complete onboarding and routine PeopleHub operations end to end.

## Phase 6 — Durable automation and integrations

1. Implement transactional outbox and concurrent-safe delivery worker with attempts, backoff, dead-letter/replay and metrics.
2. Add signed Activepieces delivery and narrow callback command endpoints.
3. Configure Zoho Mail with external secret storage and production/non-production recipient safeguards.
4. Add Moodle user/enrollment/progress synchronization and reconciliation.
5. Add authentik staff SSO behind a feature flag with break-glass local admin.
6. Publish curated, read-only, data-minimized Metabase views/role.
7. Add Cloudflare Tunnel/Access deployment config without treating it as application authorization.

**Tests:** commit/rollback outbox semantics, duplicate/out-of-order delivery, signature/replay failure, retry/dead-letter, revoked service account, sandbox email, Moodle partial failure, SSO link/deprovision, analytics role denial.

**Exit:** integrations can fail/retry safely and cannot mutate HR state outside allow-listed, audited commands.

## Phase 7 — Production readiness and staged release

1. Threat model and independent authorization review; dependency/container/secret scans.
2. Migration rehearsal against an anonymized schema copy, backup restore drill and rollback/runbook review.
3. Load/rate-limit tests for public application and uploads; scheduler/outbox failover tests.
4. GDPR retention/erasure/export tests and operational access review.
5. Synthetic end-to-end acceptance in staging, then controlled rollout with monitoring and no real mail until explicit enablement.

**Exit:** signed launch checklist, restore evidence, named operational owners, monitored staged release.

## Exact files likely to require changes

The final set depends on the Phase 0 decisions. This is the current source-based change map, not a direction to edit every upstream file.

### New TVG-owned files

- `tvg_core/`, `tvg_careers/`, `tvg_erasmus/`, `tvg_documents/`, `tvg_reports/`, `tvg_automation/`, `tvg_integrations/`: app configs, models, services, policies, migrations, admin/internal UI and tests.
- Dedicated public project/settings files, for example `tvg_careers/project/settings.py`, `urls.py`, `wsgi.py` and `asgi.py`; exact location should be decided in the CareerHub ADR.
- Purpose-built PeopleOps integration routes/serializers/services under TVG-owned apps, preferably `/api/tvg/v1/`.
- Migration and security/contract tests colocated with each app.

### Existing configuration/deployment files

- `horilla/settings/local_settings.py`: register TVG internal apps and safe client overrides.
- `horilla/settings/base.py` only if a cross-cutting setting cannot remain in TVG/client settings.
- `docker-compose.yml`, `docker-compose.prod.yml`, `docker/nginx.conf`, `.env.dist`, `Dockerfile` and `docker/entrypoint.sh`: separate services/ports/roles/storage/workers and non-production guards.
- `.github/workflows/unit-tests.yml`, `.github/workflows/quality.yml`, `.github/workflows/docker-ci.yml`, `Makefile`, `.coveragerc`: TVG tests, contract/security gates and separate service smoke tests.

### Focused upstream security touchpoints

- `recruitment/views/views.py`: published detail filter, retirement of candidate phone login, storage-safe file streaming and interview command enforcement.
- `recruitment/views/surveys.py`: submission boundary/snapshot handoff and stable/versioned survey references if retained.
- `recruitment/urls.py`: remove legacy public auth exposure and keep internal routes off CareerHub.
- `recruitment/auth.py`: deprecate/remove legacy candidate authentication after migration.
- `recruitment/cbv/interview.py` and any interview/stage actions discovered by entry-point tests: call the central gate.
- `horilla_api/api_views/recruitment/views.py`, `horilla_api/api_serializers/recruitment/serializers.py`, and recruitment API URL files: explicit read policies and allow-listed fields.
- `base/views.py`: replace path-based generic media authorization with owner-aware delivery or remove it from sensitive objects.
- `onboarding/models.py`, `onboarding/views.py` and onboarding API serializers/views: expiring single-use token state and durable onboarding transaction.
- `horilla_audit/registry.py`: additive audit floor/config behavior and TVG high-risk records.
- `base/models.py` / `base/backends.py`: only if dynamic SMTP credentials cannot be retired in favor of environment/secret references without an upstream change.

### Files that should usually remain unchanged

- `recruitment/models.py`, `employee/models.py`, `leave/models.py`, `attendance/models.py` and `asset/models.py`: prefer TVG extension/link models. Modify only for a proven invariant that cannot be enforced safely in a service/extension and document the upstream-merge cost.
- Existing historical migrations: never edit them; add new migrations.

## Recommended next implementation increment

The reusable profile, multi-application, configurable-questionnaire,
pre-interview verification, interview/decision and selected-candidate acceptance
flows are implemented and covered by isolated CareerHub tests plus PeopleOps
compatibility/security tests. The next independently reviewable slice is the
Learning Agreement handoff followed by onboarding and Employee conversion.
Before a production identity-document, interview or acceptance launch, approve
a real scanner, calendar provider, notification dispatcher, DPIA and the
retention/erasure operation documented in ADR-006, ADR-007 and ADR-008.

## Phase 16 foundation — TrainingHub and staff identity

Implemented as the isolated `tvg_training` app:

1. Reusable, approved and immutable programme versions for all seven requested
   training areas, with optional company Venture/Department/audience matching.
2. Explicit human-authorized employee assignments, stable idempotency keys and
   typed Moodle account/enrollment/progress contracts. Disabled and synthetic
   providers make production fail closed and tests network-free.
3. Existing `PeopleHubLearningAssignment` projections for course links,
   deadlines, progress and completion, plus deduplicated mandatory-overdue
   supervisor notifications.
4. Human-linked authentik subjects, approved external-role mappings that never
   mutate Horilla membership, hashed session tracking and controlled local
   session revocation. CareerHub remains independent.
5. ADR-015 and the TrainingHub/authentik integration runbook define the future
   OIDC and Activepieces/Moodle production boundary without inventing APIs.

**Still required before production enablement:** approve the real Moodle and
Activepieces API contract, implement/review its authenticated backend, configure
authentik clients and exact redirect/logout URIs, test signed OIDC logout in
staging, establish credential ownership/rotation and retain a tested local
break-glass administrator.

**Exit for this slice:** a synthetic active employee can be assigned an
approved programme, receive one idempotent mock Moodle enrollment, see progress
in PeopleHub, and have mapped-role/disabled-user/session-revocation rules tested.
