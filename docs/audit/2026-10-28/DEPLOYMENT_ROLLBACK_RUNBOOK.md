# Deployment and rollback runbook

**Status: BLOCKED. This is a future operator runbook, not permission to deploy.** Audit date: 28 October 2026. No deployment was performed.

## Responsibilities and stop rules

Name a release owner, application/security reviewer, DBA/recovery owner, infrastructure/IT operator and HR/privacy approver before release. Roles may be combined for a small team only where approvals and independent checks remain appropriate; automation does not remove human legal/signature/hiring authority. Assign a backup operator so one person's absence does not halt HR operations.

Stop for any failed authorization, identity-integrity, migration consistency, required-document, backup integrity or deprovisioning test; unexpected real messages; unknown configuration; failed restore; or unresolved high-severity dependency exposure. Do not waive a critical authorization/data-integrity failure to meet a release date.

## 1. Prepare an immutable release

- Close audit blockers, review fixes and commit all intended custom apps/settings/migrations. Record exact upstream base, fork commit, image digest and dependency/SBOM scan. Preserve existing work; do not build a release from unreviewed untracked source.
- Run the complete CI matrix under Python 3.12, PostgreSQL 16 and Redis 7, including private TVG/native tests and separate CareerHub account tests. Run `check --deploy` against each intended production settings module using synthetic staging credentials.
- Use separate staging secrets, accounts, file stores and networks. Keep `.env`, tokens, actual candidate files and approval/signature material out of code/logs/artifacts. A secret rotation plan must include the previously documented historical exposures and independent public/private signing keys.
- Disable/redirect outgoing mail, Activepieces/provider dispatch and provisioning until explicit approval. Test templates with synthetic values; company representatives must approve real legal fields/signatures separately.

## 2. Validate transport and authentication

- Inventory DNS and Cloudflare Tunnel host-to-origin routes. End with a deny/catch-all route. Use Cloudflare's ingress validation/test mechanism for the configured connector version; verify Access policy and origin firewall, not just tunnel health. [Cloudflare configuration guidance](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/).
- Fix nginx's forwarding of the external HTTPS scheme: current HTTP-only origins set `X-Forwarded-Proto $scheme` to `http`. Accept forwarding metadata only from trusted connector/proxy hops, or terminate authenticated TLS at the origin. Never trust arbitrary incoming headers merely to suppress redirects. Verify HTTP->HTTPS behavior, no redirect loops, secure cookies, HSTS scope and CSRF origin handling on each hostname.
- Derive client IP from a documented trusted chain; test spoofed forwarding headers, IPv6, direct-origin denial and rate limits across multiple workers. Cloudflare transport is not object authorization.
- Route `career.thevipgroup.eu` only to CareerHub; `people.thevipgroup.eu` only to the restricted employee portal. Provide an explicitly approved separate private HR access route for the full PeopleOps runtime; current nginx's `web` upstream is otherwise unused. Do not add broad HR routes to resolve portal 404s.
- Public signup route in source: `/candidate/create-account/`; public sign-in: `/candidate/sign-in/`; employee login: `/login/`. Test them through the actual staging proxy. These paths are not evidence that live DNS/URLs currently work.
- Verify anonymous, applicant, employee, manager, HR and IT personas across all hosts. Confirm public host has no HR/admin/media/internal routes; low-privilege users cannot use native HR APIs/media to bypass portal restrictions. Test database grants, not just URLconf isolation.

## 3. Backup, migration and release

- Follow [migration plan](MIGRATION_PLAN.md) and [recovery evidence/runbook](BACKUP_RESTORE_EVIDENCE.md). Record a coordinated recovery marker for both DBs and all document stores. Restore the checkpoint to separate targets before approving the change window.
- Pause schedulers and write-producing integrations. Run explicit one-off migrations per database and collect static assets in a versioned release path. Avoid simultaneous entrypoint migrations and shared `collectstatic --clear` while an old version serves requests.
- Start one controlled instance per runtime, keep external side effects disabled, run health/readiness and role-specific smoke checks. Health HTTP 200 alone does not prove database, mail, scan, queue or document readiness.
- Complete the joined synthetic lifecycle without direct-state shortcuts. Confirm second application stays separate, approved documents retain versions/hashes, IDs remain unique, assets reconcile, access revocation has proof, and After Mobility closure preserves history.
- Roll out gradually only with release-owner approval. Validate no schema mismatch, expected status/latency, no failed authorization, queue age and error rate. Scale only after singleton jobs and concurrent command tests pass.

## 4. Mail and automation acceptance

- Recruitment outgoing identity: `recruitment@thevipgroup.eu`; approved template version and Reply-To must be recorded. Candidate verification/reset mail currently uses static templates/default From and has no explicit Reply-To in the verification message builder: review and test both routing and approval process.
- Test SMTP DNS/connectivity/TLS/authentication only in an authorized staging environment; capture a fake message in a local/test sink. Verify SPF/DKIM/DMARC and bounce handling with the domain owner. Do not send real candidate mail as a connectivity test.
- Import reviewed, secret-free Activepieces configurations into the target CE version and verify they are runnable exports, not merely design manifests. Bind staging credentials outside the export. Exercise acknowledgement, verification request and document reminder with signature tampering, replay, duplicate callback, retry, dead-letter, cancellation and withdrawn-candidate cases.
- Validate a durable consumer idempotency store before enabling external delivery. Horilla's event remains authoritative; Activepieces receives bounded commands/projections, not DB write credentials. Test fast callbacks, timeout after a side effect, retry after worker restart and approved replay. Do not infer “exactly once” email from a successful HTTP request.
- Keep manual tracked IT tasks when the provider has no approved API. Test disabled accounts and session invalidation independently of SSO login/role mapping. No production Moodle, authentik, mail, signing or scheduling integration is accepted solely on mock contract tests.

## 5. Monitoring and daily operations

- Structured logs should carry safe request/event/correlation identifiers, service/version, outcome and latency, never passwords, bearer credentials, verification/reset URLs, CVs, medical data or legal document bodies. Verify nginx/Gunicorn/Cloudflare/APM redaction as well as application logging.
- Alert on queue age > 5 minutes, terminal workflow failures, missed scheduler heartbeat > 3 minutes, scan failures/pending backlog, backup age exceeding the approved RPO, restore validation failure, storage/DB exhaustion, elevated 5xx and repeated authorization denials. Thresholds are proposed initial values; tune from staging evidence.
- Use a single scheduler with an enforced deployment singleton or leader lock; `max_instances=1` only limits one process. The native Google Drive backup scheduler is still an in-process exception and must be relocated or kept disabled under a reviewed replacement backup procedure.
- Daily HR exception queue: overdue documents/approvals, failed/reminded workflows, interviews awaiting human decisions, upcoming arrivals/departures, unreturned assets and missing access-deactivation proof. Design it for exception handling rather than manual duplicate entry; measure backlog before promising one-person staffing capacity.
- Test alert delivery and escalation to named owners. Keep dashboards aggregate and permission-scoped; Metabase reader must not have raw HR or candidate-table access.

## 6. Rollback

1. Declare an incident/change rollback and stop new writes, jobs, mail and provisioning for the affected release. Record event/outbox boundary and intended recovery point without copying live payloads into general logs.
2. For compatible additive schema, return to the prior approved image and retain schema; verify old code against current data. Otherwise use the tested paired-DB/private-file restore into new targets described in the migration plan.
3. Validate counts, constraints, identities, document hashes/versions, asset status, role mappings and revoked-user denial. Reconcile idempotency records against any external effects already completed. Do not replay the whole outbox automatically.
4. Reopen limited traffic, then allow one scheduler and individually approved integrations. Monitor for the agreed observation period. Retain the failed environment for restricted investigation; never delete volumes or historical legal records as part of automatic rollback.
5. Record recovery elapsed time, recovered checkpoint, actual data loss and business reconciliations. Obtain HR/DBA/release-owner sign-off before reopening all write paths.

This runbook remains unexecuted against deployed infrastructure; the release checklist records outstanding evidence.
