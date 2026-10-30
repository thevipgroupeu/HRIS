# Unresolved risks and required fixes

Date: 28 October 2026. Decision: **NO-GO**. Owners below are responsibility categories to assign, not claims that a specific person has accepted the work. “Confirmed” means reproduced or directly visible in reviewed source; “unverified” means no acceptance evidence, not proof the deployed system lacks the control.

| ID | Priority / evidence | Required action and acceptance | Proposed owner |
|---|---|---|---|
| SEC-01 | High, reproduced | Enforce owning-object authorization on native private-media reads; unrelated employee/JWT requests must return 403/404 without bytes. Review public candidate photo prefix. | Application/security |
| SEC-02 | High, reproduced | Enforce permission + tenant + object scope on native Candidate detail and audit adjacent detail APIs. Low-privilege GET test must deny. | Application/security |
| SEC-03 | High, reproduced | Bind application status/withdrawal to configured legal employer and matching Person/opening; foreign application unchanged and hidden. | Integrations/security |
| SEC-04 | High for runtime DRF, toolchain findings separately assessed | Upgrade vulnerable pins/build tools, test fixed versions, scan final image and approve only scoped, expiring mitigations for no-fix advisories. Do not treat ignored pdfkit advisory as resolved. | Release/security |
| SEC-05 | High, reproduced guard defects | Reject production DEBUG, weak secrets and any wildcard host entry; deploy checks must pass on all runtime settings with safe synthetic credentials. | Platform |
| SEC-06 | High, source-confirmed/unverified integration | Real malware scanner/quarantine with fail-closed download/review; reject fake scanners in production and cover all upload classes. | Security/IT |
| SEC-07 | High, source-confirmed logging risk | Redact token-bearing URL paths/referrers at every log hop; test canary-token absence and secret rotation/history scan. | Platform/security |
| MIG-01 | Medium, release gate failed | Add reviewed CareerHub security-event-choice migration. Run consistency gates on both app registries, not only private Horilla. | Application |
| OPS-01 | High, source-inferred availability risk | Repair trusted HTTPS/client-IP forwarding; validate Cloudflare ingress/catch-all/Access/firewall and prove no redirect loops or header spoofing. No live route claim yet. | Infrastructure |
| OPS-02 | High, backup scope incomplete | Cover both databases and every private root; prove coordinated, encrypted, off-site restore within approved RPO/RTO. Local sparse restore passes but does not close this. | DBA/IT |
| OPS-03 | High, evidence missing | Commit/review intended TVG work; build immutable artifact with lock/SBOM, provenance and independent release sign-off. Working-tree HEAD is not customized release. | Release owner |
| OPS-04 | High, test coverage gap | Add TVG and independent public settings to CI; PostgreSQL/Redis/3.12 concurrency tests and continuous fictional journey without direct-state shortcuts. | QA/application |
| OPS-05 | Medium, source-confirmed route gap | Decide approved private HR entry point. Current supplied nginx serves restricted PeopleHub and CareerHub, not full HR `web` routes. Verify exact links under intended host, without opening HR publicly. | Infrastructure/HR |
| OPS-06 | Medium, source-confirmed and operationally unverified | Enforce scheduler singleton/leadership and job idempotency; address native Google Drive in-process backup exception; prove restart/missed-job alerting. | Platform |
| OPS-07 | Medium, integration acceptance missing | Approve templates, SMTP/Reply-To/bounce controls and test mail sink; execute first three real staging Activepieces flows with fake recipients and signatures/retries/cancellation. | HR/integrations |
| OPS-08 | Medium, evidence missing | Prove external account revocation/session invalidation and manual proof review; mock success does not establish provider behavior. Keep authorized manual IT fallback. | IT/security |
| PRIV-01 | High before collecting real sensitive documents, incomplete governance | Approve data inventory, lawful basis/notices/retention/holds, processor access and DSR export/rectification/erasure/restriction handling across both DBs, copies, logs and backups. Test holds and reconcile deletion exceptions. No blanket auto-delete. | Privacy/HR |
| UX-01 | Medium, unverified | Browser accessibility/mobile testing, keyboard/screen reader/error focus/long uploads/320px tables; fix serious issues before candidate launch. Source review alone is not conformance. | UX/QA |
| PERF-01 | Medium, unverified | Run approved representative staging load with real password hashing, DB constraints and background workload; meet provisional targets or agree revised capacity. | Platform/QA |
| OPS-09 | Medium, configuration risk | Set/verify Europe/Warsaw business timezone deliberately; .env.dist still uses Asia/Kolkata. Test DST and scheduled deadlines without silently altering existing agreement dates. | Platform/HR |
| OBS-01 | Medium, unverified | Prove log redaction, privacy-safe metrics, failure/backlog/backup/scan alerts and escalation ownership. Health endpoints alone are insufficient. | Operations |

## Recommended fix order

1. Close the reproduced access-control failures, runtime dependency findings, unsafe config guards and migration drift. Preserve the failing probes as regression tests; extend them rather than weakening assertions.
2. Establish safe document scanning, log/secret handling and an immutable CI-tested release. Review all full-HR native endpoints before granting ordinary employees any path to them.
3. Fix routing/HTTPS, make paired backups complete, rehearse recovery and enforce scheduler ownership. Keep external side effects disabled while testing.
4. Complete the continuous fictional lifecycle in two-runtime staging, including independent second application, approved legal/document flows, real authorization boundaries and synthetic external effects.
5. Complete privacy, accessibility, capacity and operational sign-offs. Only then request a separate deployment decision.

## Staffing expectation

The outbox, reminders, document state tracking, dashboard and manual-task fallback can reduce repetitive work, but this audit cannot support a promise that one person can safely manage all HR. Mandatory human decisions/signatures, exception handling, access revocation and privacy requests remain. Measure exceptions per week, average handling time, overdue queue and backup coverage during staging/pilot; assign backup coverage even if one person is the day-to-day operator.
