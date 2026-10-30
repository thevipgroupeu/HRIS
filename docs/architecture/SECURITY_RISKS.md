# Security Risks

## Rating and scope

Ratings are source-audit priorities, not penetration-test findings. Deployment controls may change likelihood, but they were not verified. Owners and due dates should be assigned before implementation.

| ID | Severity | Risk and evidence | Required treatment |
|---|---|---|---|
| SR-01 | Critical | Public recruitment, staff HR, API and `/admin/` share one root URL surface. No host-specific routing exists. A proxy rule mistake exposes private endpoints. | Separate processes/settings/root URLconfs and add negative host-route tests. Restrict PeopleHub with application auth plus Cloudflare Access/Tunnel. |
| SR-02 | High | The audited production Compose published web on `0.0.0.0:8000`, permitting nginx/tunnel bypass if the host network allowed it. Phase 3 removes this binding from the production overlay; an old or manually modified deployment may still expose it. | Verify the effective production Compose output, host listeners and firewall after rollout; retain only nginx/tunnel ingress. |
| SR-03 | High (legacy), mitigated for CareerHub | Horilla's legacy candidate backend still exists upstream and treats email plus phone/last ten digits as credentials. PeopleOps now masks its login and candidate self-tracking entry paths, while CareerHub uses a separate verified account, but an alternate URLconf or deployment regression could expose the legacy views. | Keep route-denial regression tests and deployed route inventory; remove/deprecate the upstream backend only with an upgrade-compatible migration plan. Never mount it on CareerHub. |
| SR-04 | High | Recruitment API Candidate/CandidateDocument detail GETs require authentication but not the corresponding view/object permission; serializers expose all model fields. | Centralize tenant + permission + object policy, allow-list response/write fields and test same-company low-privilege access. |
| SR-05 | High | `protected_media` authorizes any authenticated staff/JWT for any known non-public media path, not the owning domain object. | Resolve authorized objects before streaming; use opaque ids/private object storage/short-lived delivery. |
| SR-06 | High | Onboarding bearer tokens are plaintext and have no expiry; public steps query token alone. Pending user state is process-global and breaks across workers/restarts. | Store hashed, expiring, single-use tokens and durable state; bind transitions to the case/session and audit replay/failure. |
| SR-07 | High | Candidate/university documents lack malware scanning, quarantine, content-signature validation, formal retention/erasure and consistent remote-storage streaming. | Add private storage, scan/quarantine pipeline, safe type policy, retention/erasure jobs and authorization tests. |
| SR-08 | High | Database fields hold SMTP passwords and other integration tokens (for example DynamicEmailConfiguration and LinkedInAccount). Repository CI also records historical secret leakage. | Inventory/rotate all credentials, move values to a secret manager/environment references, encrypt unavoidable stored secrets, and perform a separate full-history response plan. |
| SR-09 | High (mitigated for CareerHub-linked applications) | Interview creation or movement to an interview stage could bypass a screen-only verification check. | `tvg_erasmus` now enforces one eligibility policy from the schedule form plus model pre-save guards for InterviewSchedule and Candidate interview-stage transitions. Keep entry-point regression tests; direct SQL remains an operationally privileged bypass and no override exists. |
| SR-10 | Medium | Public recruitment detail filters by numeric id but not `is_published`/active, enabling unpublished job metadata enumeration. | Apply the same publication predicate as the list/application endpoint and test direct id access. |
| SR-11 | Medium | Horilla company manager is context-dependent and unfiltered when no company is resolved. Public/background code sometimes needs the unscoped `default` manager. | Ban implicit unscoped access in TVG services; require explicit tenant or deliberately named system query, with tests and review rule. |
| SR-12 | Medium | In-process automation threads are lossy, lack durable retries/idempotency and may execute after the request without reliable operational state. | Transactional outbox plus worker, signed payloads, delivery history, retry/dead letter and idempotent consumers. |
| SR-13 | Medium | Scheduler jobs are documented as non-idempotent; a second scheduler can duplicate payslips/backups and other effects. Compose `deploy.replicas` is not a universal lock. | Add deployment singleton enforcement/leader lock, metrics/alerts, then make high-impact jobs idempotent. |
| SR-14 | Medium | Default audit tracking omits recruitment/onboarding/candidate documents/interviews/assets; any configured rows replace the built-in defaults wholesale. | Make a non-removable risk-based audit floor, add workflow events, protect audit administration and monitor disabled coverage. |
| SR-15 | Medium | Candidate profile images are public by storage prefix and numeric/path knowledge may expose PII. | Remove public profile prefix unless strictly needed; use authorized/proxied images and data minimization. |
| SR-16 | Medium | Application/survey continuation serializes candidate state in a session and answers are keyed by mutable question text; snapshot/provenance are weak. | Use stable question/schema ids and immutable application snapshots with consent/document versions. |
| SR-17 | Medium | Existing broad API and long-lived refresh credentials are unsuitable for Activepieces/CareerHub. Service compromise could overreach. | Purpose-built endpoints, separate short-lived service credentials/scopes, mTLS/signatures where appropriate, IP/network limits and revocation. |
| SR-18 | Medium | Default timezone is Asia/Kolkata. Incorrect local deadlines/timestamps can affect interviews, leave, retention and audit interpretation in Poland. | Configure `Europe/Warsaw`, store UTC, test DST boundaries and display timezone explicitly. |
| SR-19 | Medium | No verified GDPR lawful-basis, privacy-notice versioning, retention, export, rectification or erasure workflow was found for candidates. | Legal/privacy decision, data inventory/DPIA, consent/notice ledger, retention holds and tested data-subject workflows. |
| SR-20 | Medium | Metabase, Moodle, authentik, Activepieces and Cloudflare are absent from the stack; ad-hoc deployment could create overprivileged trust paths. | Threat-model each adapter, use separate identities/roles, deny direct HR DB access except curated Metabase views, and monitor/revoke. |
| SR-21 | Medium | AI resume-ranking code exists, while the required human-decision boundary is policy only. Future automation could turn advisory output into status changes. | Label/provenance AI output, prohibit command permissions, require human confirmation and audit model/input/version/decision. |
| SR-22 | Low | No local release tags or upstream remote are configured, weakening provenance and security-patch tracking. | Configure authoritative upstream read-only remote, fetch signed release tags and record fork-base commits. |

## Phase 3 CareerHub controls added

- CareerHub now has a separate settings module, WSGI process, root URLconf, secret/cookie names and Compose service. Automated negative tests assert that admin, employee, broad API, internal integration and media paths return 404 on the public URL tree. This materially mitigates SR-01 for the new surface; deployed route evidence is still required.
- The new job API uses an explicit safe projection and applies the same published/active/closed predicate to list and detail calls, mitigating SR-10 for CareerHub without claiming that every legacy Horilla public recruitment route is remediated.
- The integration is fail-closed to one configured legal-employer id, exact bearer authentication and nginx denial on the external PeopleOps proxy. The initial static bearer remains a residual SR-17 concern until secret rotation or workload identity/mTLS is implemented.
- The production overlay no longer publishes Horilla's port 8000 on the host, closing the direct nginx/hostname bypass represented by SR-02 for new deployments. Deployed listener and firewall evidence is still required.
- CareerHub has no application upload or submitted-candidate data route in this phase.

## Phase 4 candidate-authentication controls added

- `CandidateAccount` is an authentication principal in a dedicated CareerHub database, not a replacement Horilla Candidate or staff user. It has no group, staff, superuser or permission capability.
- Signup requires a versioned privacy-notice acknowledgement. Optional talent-pool consent is separate, timestamped and withdrawable from the owner-only profile.
- Django password hashing/validation, signed expiring email verification, one-time expiring recovery tokens, session-id rotation, POST-only CSRF-protected logout and authentication-hash invalidation are covered by automated tests. Passwords and tokens are not logged or emailed.
- Signup, login, verification and recovery have HMAC-keyed Redis counters. Generic duplicate/recovery responses reduce account enumeration; synchronous email timing remains a residual side channel to review before high-risk launch.
- Person provisioning uses a separate service credential and configured legal employer. It is idempotent for the proposed UUID and refuses email-only auto-merge. A conflict enables profile access but keeps application functions disabled pending a future human reconciliation workflow.
- Production configuration fails closed without a separate PostgreSQL database, Redis, distinct strong read/identity tokens, a calibrated trusted-proxy count and a non-development SMTP backend. Development Compose explicitly uses the local-memory email backend.
- Residual operational evidence is required: run the account migration against the dedicated CareerHub database through the approved release process, verify DB grants and proxy/client-IP handling, configure secret rotation, and test Zoho delivery only in an authorized staging environment.

## Phase 5 candidate-profile/application controls added

- Reusable profiles and documents live only in the candidate's isolated
  CareerHub account database/storage. Every profile, document, draft,
  submission download and withdrawal resolves the owner from the authenticated
  session; opaque ids alone grant no access.
- Reusable PDF/DOCX files are versioned. Submission makes separate immutable copies
  plus a JSON profile/questionnaire copy, preventing later profile or reusable
  file edits from rewriting an earlier application.
- Uploads are limited to 5 MB PDF/DOCX documents and checked for matching type,
  safe structure and known active content. This is not antivirus scanning:
  SR-05 remains open until production
  quarantine/scanning and retention/erasure automation are approved.
- A dedicated application service token is distinct from catalogue-read and
  identity tokens. PeopleOps rechecks employer, stable Person/email, opening
  publication and dates, and uses the opaque application id for retry-safe
  creation.
- Candidate-visible status is explicitly projected. Reviewer comments,
  interview descriptions/interviewer identities, ratings, AI assessments,
  staff data and other candidates are not returned.
- Withdrawal is POST/CSRF protected in CareerHub, owner-scoped at both services
  and idempotent in PeopleOps. Horilla's cancelled-stage behavior retains the
  authoritative candidate history.
- Private CareerHub media has a dedicated persistent volume and no public media
  route. Authorized downloads send `private, no-store` and `nosniff` headers.

## Configurable-questionnaire controls added

- Native Horilla survey templates remain company-scoped authoring records;
  cross-company assignments fail closed. Public projections use opaque question
  UUIDs and exclude native ids.
- Immutable definition versions and full submission snapshots mitigate SR-16.
  PeopleOps requires the exact current UUID/version/fingerprint tuple and
  rejects stale or tampered submissions before creating a Candidate.
- Generic questionnaire file fields are blocked from CareerHub, preventing
  accidental identity-document collection in the initial application.
- Application source is enumerated and UTM storage is restricted to five
  bounded keys. Unknown campaign fields are discarded.
- Confirmation is a transactionally committed, data-minimized, idempotent
  outbox event. Phase 15 now wraps it in `tvg_automation` for signed dispatch,
  retry, failure review, cancellation and guarded replay. Deployment evidence
  against the target staging Activepieces instance is still required.
- Questionnaire responses never trigger automatic eligibility, hiring or
  rejection decisions; SR-21 remains governed by human-only workflow commands.

## Pre-interview verification controls added

- Verification begins only after a human shortlist decision. Erasmus+ openings
  fail closed unless the configured checklist requires identity, student-status
  and university confirmation. No protected characteristic produces an
  automatic rejection or hiring decision.
- Evidence uses UUID-only paths in a dedicated private volume with no public
  storage URL or nginx mount. Candidate uploads and HR downloads both recheck
  application ownership, tenant, manager assignment and permission. Only clean
  evidence can be downloaded or reviewed.
- PDF/JPEG/PNG content is bounded and structurally checked. The default scanner
  leaves uploads quarantined as `pending`; it must be replaced by an approved
  production scanner. Until then, the interview gate fails closed.
- Native Candidate document rows contain request/status metadata only. Identity
  evidence bytes are not copied into general Horilla media, automation events,
  logs, email or AI systems.
- Screening, evidence and review transitions have immutable audit records.
  Candidate projections exclude internal notes, reviewer identities, native
  ids, evidence paths and other candidates.
- Verified/rejected cases and withdrawn/rejected applications receive a
  configurable deletion date. The explicit purge command deletes bytes while
  retaining minimal audit metadata; production scheduling awaits DPIA and
  retention-owner approval.
- A dedicated verification bearer token is distinct from catalogue, identity
  and application tokens. The production scheduler disables every CareerHub
  HTTP API surface.

## Interview scheduling and decision controls added

- Invitation and booking services re-evaluate the central Phase 7 verification
  gate. CareerHub knows only opaque application/invitation/slot identifiers and
  a candidate-safe projection; it never receives feedback, ratings, internal
  rationale or other candidates.
- Bookings are timezone-aware and lock invitation/slot rows. Candidate and
  interviewer overlaps are rejected before the native Horilla schedule is
  created. Europe/Warsaw and the candidate's validated IANA timezone are both
  displayed to prevent ambiguous local-time decisions.
- Candidate rescheduling is limited to one and requires 12 hours' notice.
  Short-notice staff changes require a separate override permission and leave
  an immutable event. Candidate booking/rescheduling is session/CSRF protected,
  owner-scoped at both services and rate limited.
- Calendar adapters receive only scheduling identifiers/times and an
  idempotency key. Zoho and self-hosted provider keys fail closed until an
  approved adapter is installed; automated tests use a deterministic no-network
  provider.
- Interviewer feedback is staff-only and object-scoped. Decisions require an
  authenticated human after completion. Rejections additionally require a
  human-approved, recruitment-scoped message template. No AI/automation command
  can enact an outcome.
- Notification records are an unsent outbox in this phase. Real recipient
  delivery, retries and suppression require the future `tvg_automation`
  dispatcher and non-production recipient safeguards.

## Selected-candidate arrangement and acceptance controls added

- An Erasmus case requires the immutable human-selected interview decision and
  links to the existing Application and Candidate. Candidate date commands are
  owner-scoped again in PeopleOps and reject inactive applications and overlaps.
- The normal three-calendar-month duration is legal-employer configuration.
  Shorter confirmed dates are never rewritten: they block HR approval until a
  different authorized employee approves a date- and policy-bound exception.
- HR approval fails closed on candidate, university, funding/eligibility,
  curriculum and duration requirements and creates an immutable arrangement
  snapshot. No incomplete case can issue an acceptance letter.
- Legal issuer, registration and signatory text must be entered in a company
  template. Approved template versions and issued documents are immutable; no
  code default invents missing legal or signature data.
- PDF issue and portal delivery are separate permission-checked human actions.
  Only the latest document version can be sent, and only the owning candidate
  can download or acknowledge it. Private responses are `no-store` and PDF
  storage uses opaque UUID paths.
- “Send” writes an idempotent outbox event and does not call email. Production
  delivery, retry, suppression and recipient safeguards remain blocked on the
  Phase 6 automation dispatcher.

## Erasmus document lifecycle controls added

- Learning Agreement and company-document files use a separate private storage
  root, opaque paths, structural PDF/DOCX checks, immutable digest-bearing
  versions and mediated downloads. This does not close SR-07: production malware
  scanning and approved retention/erasure automation are still required.
- Candidate commands re-resolve Person ownership and legal employer; staff
  commands require tenant, recruitment relationship and action permission.
- Approved template copies remain drafts. Review, approval, signature
  attestation and return are separate human actions, and signatures bind to the
  latest version.
- External signing and NA10 adapters fail closed by default. No NA10 requirement
  semantics or outbound data transfer has been invented.
- Accommodation follow-up stores a boolean, status and audit actors/timestamps,
  not free-text housing or other personal circumstances.

## Intern onboarding controls added

- Employee conversion is blocked until acceptance, dates, Before Mobility
  documentation and a separate HR onboarding approval are complete. The stable
  Person keeps its personal email while Employee receives only a confirmed work
  address.
- Manual, Zoho and self-hosted provisioning adapters fail closed. Unsupported
  automation creates a manual IT task; passwords, access tokens and mailbox
  payloads are neither accepted nor stored.
- Department/venture permissions come from a human-approved immutable group-id
  snapshot and become company-scoped Horilla group assignments. Service checks
  enforce action permission and legal-employer scope.
- Equipment uses native serial-bearing AssetItem/AssetAssignment rows. A TVG link
  adds only the approved acknowledgment document and audit state. Private PDFs
  have mediated downloads and no public media URL.
- Welcome/arrival records are schedules, not mail delivery. Real notification
  side effects remain disabled pending the durable automation boundary.

## PeopleHub portal controls added

- The employee hostname routes to a dedicated process and URLconf that omit
  native HR administration, API and direct media endpoints. Separate session and
  CSRF cookie names prevent accidental session-boundary coupling.
- Directory data is assembled from a fixed work-field allowlist; model objects
  containing personal, identity, medical or payroll data are not passed into the
  shared directory template.
- Employee detail, manager team and privileged queue access combine native
  permission checks with explicit company and relationship checks. A permission
  never overrides the legal-employer boundary.
- Portal rendering excludes Horilla's global administrative menu context. Custom
  403/404 handlers prevent rejected or unknown paths from rendering that menu.
- Internal request routes require human approval, are immutable after approval,
  and create native helpdesk tickets without an implicit email side effect.
- Authentik remains a disabled adapter. No external endpoint, claim mapping,
  credential, automatic account link or deprovisioning rule is assumed.

## Internship reporting controls added

- Weekly and monthly objects enforce owner, reporting-manager/custom permission
  and legal-employer checks in services and views. Management permission never
  overrides the tenant boundary.
- Every submission and review is immutable and version-bound. Approved records
  and rubrics cannot be edited; revision-required submissions create a new
  version and retain the previous reviewer action.
- Presentation files accept only structurally valid, non-active PDF/PPTX content,
  use a private reporting volume and are streamed only after object authorization
  with `nosniff` and `no-store`. Production malware scanning remains an open
  pre-launch requirement.
- AI support is disabled by default and stores provider/model/prompt provenance.
  Its schema has no authority to approve, discipline, rank or select interns;
  report-writing commentary and actual performance commentary remain distinct.
- Notification delivery is explicit, deduplicated and scheduler-owned. This
  phase creates no email side effects and stores no credentials in report links,
  files, fixtures or audit metadata.

## Internship completion and offboarding controls added

- Completion cases remain company-scoped and link the permanent Person,
  recruitment Application, Employee, InternOnboarding and native
  OffboardingEmployee records without copying or deleting their history.
- The completion service revalidates mandatory reporting, university-document,
  native asset-return and access-revocation evidence under a database lock.
  Final closure is unavailable before the confirmed end date or while a
  mandatory item remains unresolved.
- Completion and asset-return PDFs are derived only from approved immutable
  template versions. Company authorization uses a dedicated permission, each
  human signature is timestamped, and only the latest certificate version may
  be issued.
- Completion files and revocation proof use an isolated private volume.
  Downloads require owner/manager/company-object access and return `no-store`
  and `nosniff`; audit events store references rather than document contents or
  credentials.
- Access adapters are disabled by default. Unsupported APIs create a tracked
  manual task, and closure requires non-secret proof or a reasoned exception
  approved with a separate permission.
- Versioned retention policies only calculate a review date. No scheduler,
  signal or completion action deletes historical recruitment, internship,
  university or approved legal records.

## Required security test matrix

Every new public or integration endpoint must cover:

- anonymous, wrong-principal-type, expired/revoked credential and brute-force/rate-limit behavior;
- cross-candidate, cross-tenant and same-tenant-without-permission access;
- list and detail consistency, including guessed numeric/opaque ids;
- field-level disclosure and mass-assignment attempts;
- CSRF/session fixation, host-header/origin and cookie-boundary behavior;
- upload extension/MIME/content/size, malicious active content, quarantine and path traversal;
- duplicate, replayed, delayed and out-of-order events/commands;
- atomic rollback, concurrent approval/interview attempts and idempotent retries;
- audit actor/time/reason before-and-after state without logging sensitive payloads;
- non-production mail/integration sink enforcement.

## Pre-production evidence required

1. Deployed route inventory for both hostnames and proof that CareerHub returns 404 for admin/staff/broad API/private media routes.
2. Database grants showing the CareerHub and Metabase roles cannot read HR base tables/files.
3. Object authorization test report and independent review of candidate/document endpoints.
4. Token/session key rotation, service credential revocation and break-glass runbooks.
5. Backup restore and applicant-document erasure/retention evidence.
6. Full secret inventory/rotation confirmation, handled outside this repository and without copying secrets into tickets/docs.
7. Threat model/DPIA approval and named incident, privacy and integration owners.

## Safe development controls

- Use synthetic people/files only and a separate database/media namespace.
- Set email to local-memory/file/sink and deny non-allow-listed recipients in code and network policy.
- Disable external adapters by default and require explicit sandbox configuration.
- Never load production dumps unless formally anonymized and authorized.

## Reliable automation controls added

- Lifecycle payloads are versioned and data-minimized; recipient addresses and
  approved rendered messages are resolved only for the signed delivery body and
  are not copied into delivery-attempt logs.
- Outbound delivery and inbound callbacks use independent environment-owned
  HMAC secrets. Callback timestamps expire, body hashes are retained and
  idempotency keys reject conflicting replays.
- Activepieces has no database credential and no generic mutation endpoint.
  Callback input can record only an allow-listed execution state for a known
  lifecycle event and workflow pair.
- Human-approval requirements are declared in Horilla's event catalogue and
  checked again before dispatch. Approved message-template versions are
  immutable; later edits require a new version and new human approval.
- Development automation is disabled by default. The synthetic transport used
  by tests performs no network or SMTP call, and test settings retain Django's
  local-memory email backend.
- Failed events retain immutable attempts and require a dedicated permission,
  company match and recorded reason before replay. Delivered or cancelled
  events cannot be replayed.

## TrainingHub and staff identity controls added

- Moodle course delivery remains outside Horilla. Approved programme versions
  are immutable, while assignments retain human authorizer/time and stable
  account/enrollment idempotency keys.
- Normal configuration fails closed through a disabled provider. The synthetic
  provider is test-only and the security check rejects it in staging or
  production; no Moodle, authentik or Activepieces credential is stored in the
  programme, assignment, audit or PeopleHub tables.
- Provider requests use the confirmed company email and opaque staff/company/
  course references. Horilla stores bounded progress and completion summaries,
  not Moodle activity payloads, grading details, tokens or course content.
- authentik subjects cannot create staff records or grant roles. An authorized
  human links the subject to an existing active Employee; approved external-role
  mappings are effective only where the same user already has the matching
  Horilla `CompanyGroupAssignment`.
- Staff session records contain SHA-256 hashes rather than session keys or
  provider session identifiers. Revocation increments a session version and
  terminates matched Django sessions; a production OIDC logout endpoint must
  validate signed issuer/audience/event claims before calling this service.
- CareerHub remains a separate URLconf, credential/session surface and Person
  provisioning boundary. It exposes neither PeopleHub nor TrainingHub routes.
- Never run destructive Compose/Make targets, production migrations or credential rotation as part of ordinary development/testing.
