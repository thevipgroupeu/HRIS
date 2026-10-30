# THEVIPGROUP PeopleOps production-readiness audit

Audit date: 28 October 2026 (Europe/Warsaw). Decision: **NO-GO for production**.

Four new authorization regression tests fail, three configuration guard checks fail, the separate CareerHub migration check fails, and the installed dependency scan reports security advisories. Existing passing tests do not override these failures. No deployment, production migration, live recruitment change, real email, real offer, or real account provisioning/revocation was performed. Application/runtime source was not fixed in this audit.

## Deliverables

- [Security test report](SECURITY_REPORT.md)
- [Functional test report and requested journey coverage](FUNCTIONAL_REPORT.md)
- [Migration plan](MIGRATION_PLAN.md)
- [Backup/restore evidence](BACKUP_RESTORE_EVIDENCE.md)
- [Deployment and rollback runbook](DEPLOYMENT_ROLLBACK_RUNBOOK.md)
- [Unresolved risks and required fixes](UNRESOLVED_RISKS.md)
- [Production release checklist](RELEASE_CHECKLIST.md)

## Evidence scope

Reviewed working tree: `C:/Users/DELL/Desktop/HRIS`. Base commit: `9c97e785f505a6dc2feae2312609ee28f32993b0` on `2.0`. Most TVG extensions/docs are untracked and several upstream/configuration files are modified. This is a working-tree audit, not certification of an immutable release artifact. Existing user changes were preserved.

Local application tests ran on Python 3.13.14 / Django 5.2.18, with an isolated in-memory SQLite database, temporary media, fake integration adapters and local-memory email. `.env` loading was disabled and inherited service configuration removed by the diagnostic harness. Python 3.12 / PostgreSQL 16 / Redis 7 release-equivalent CI remains required. A separately named disposable PostgreSQL 16 container was used only for synthetic migration/restore checks; no existing database or volume was used.

Fresh results:

| Check | Result | Evidence |
|---|---|---|
| TVG private applications | 239 passed | `evidence-private.json` |
| Separate CareerHub account/portal suite | 37 passed | `evidence-public.json` |
| Selected native authorization/tenancy tests | 51 passed | `evidence-native.json` |
| New authorization regression probes | **4 failed** | `evidence-probes.json` |
| Negative configuration checks | **3 failed** | `evidence-configuration.json` |
| Private migration consistency and empty SQLite migration graph | Passed | `evidence-migrations.json` |
| Private checks/tenancy/migrations on PostgreSQL 16 | Passed | `evidence-pg_migrations.json` |
| Separate CareerHub migration consistency | **Failed: missing security-event choice migration**; existing graph applied only for disposable restore test | `evidence-public_pg_migrations.json` |
| Synthetic backup toolchain and eight file-root hash checks | Passed, limited scope | `evidence-backup.json` |
| Both migrated application DBs restored to fresh synthetic targets | Passed: 528/12 tables and 520/8 sequences match, with views/constraints/data | `evidence-application-restore.json` |
| Small analytics query benchmark | Passed reconciliation, not capacity certification | `evidence-benchmark.json` |
| Installed-package audit | **12 vulnerability entries, including repeated IDs; 9 distinct IDs across 5 packages** | `evidence-dependencies.json` (minimized metadata) |

The 327 passing application tests are separate test cases, not one continuously linked candidate journey. The additional benchmark is one diagnostic test. See the functional report for exactly which boundaries remain unproven.

Audit scripts pass Ruff, Black, isort and compilation checks. They are diagnostic tools, not deployment entrypoints. To rerun the in-memory checks from this repository with its installed dependencies, use `.venv/Scripts/python.exe docs/audit/2026-10-28/run_checks.py` followed by `private`, `public`, `native`, `probes` or `benchmark`. `configuration_checks.py` is independently executable. The failing probes/configuration checks are expected to remain red until application fixes land. PostgreSQL modes require the explicitly labeled disposable container and fresh named audit databases; never repoint them to a deployed database. Do not rerun the one-shot restore scripts against existing targets.

## Coverage of the 18 requested areas

| Area | Assessment |
|---|---|
| 1. Horilla/upstream/security updates | Base matches upstream `2.0` at audit; 2.2.0 ancestry verified. Dependency security updates pending. Development branch divergence not certified. |
| 2. Django configuration | Configuration guard failures reproduced; effective production settings not inspected. |
| 3. Candidate authentication/documents | Public account suite passes; native CV and Candidate detail authorization fail. |
| 4. CareerHub/PeopleHub isolation | Separate public account DB/settings/cookies/routes in source; internal application employer boundary fails. Deployment/network grants unverified. |
| 5. Staff/object permissions | PeopleHub/team/tenant tests pass; full native HR surface remains unsafe for low-privilege principals. |
| 6. Migrations/rollback | Private graph passes; separate CareerHub has migration-state drift. Rollback strategy and rehearsal requirements documented separately. |
| 7. PostgreSQL backup/restore | Synthetic restore verified; production coordinated recovery remains unproven. |
| 8. File backup/restore | Eight synthetic roots hash-match; shipped backup coverage is incomplete. |
| 9. Secrets | Environment separation exists; weak-value guards and historical rotation evidence incomplete. No live secrets inspected/exported. |
| 10. Cloudflare Tunnel | Supplied nginx origin-protocol handling is inconsistent with HTTPS forwarding; actual tunnel/Access policies unverified. |
| 11. SMTP/templates | Local-memory/fake tests only; no production connectivity or real mail test. Approval and Reply-To requirements documented. |
| 12. Activepieces | Signing/idempotency/retry/cancellation tests pass using fake transport; installed CE flows not exercised. |
| 13. Jobs/scheduler | Dedicated scheduler exists; singleton enforcement, backup scheduler exception and live alerting require work. |
| 14. Logs/monitoring | Structured app logging exists; access-log URL/token exposure and monitoring evidence gaps remain. |
| 15. GDPR/retention | Some versioned notices, purge and retention-review support; end-to-end data-subject/hold/erasure handling not demonstrated. Not a legal-compliance certification. |
| 16. Accessibility/mobile | Source review only; browser, keyboard, screen-reader and small-viewport acceptance outstanding. |
| 17. Automated coverage | Passing focused suites, but default CI smoke/coverage labels omit TVG suites and independent CareerHub runtime. |
| 18. Performance | Small single-process query measurement only; proposed workload/recovery targets not certified. |

## Provisional acceptance targets

In response to the owner's “do what's best,” use 500 applications/day, 50 simultaneous active users, and a representative 10,000-application dataset as an initial staging load profile. Target p95 interactive response under 2 seconds, p95 supported 5 MB upload under 5 seconds, and less than 1% unexpected server errors during a 30-minute run. Exercise a 100-user short burst separately, including bounded overload behavior and rate limits. These are engineering planning assumptions, not a business forecast or measurements from this audit.

Propose RPO <= 1 hour and RTO <= 4 hours for both databases and private files. A daily dump alone cannot meet that RPO. Legal-record retention durations require controller/legal approval and are deliberately not invented here.

Stop condition: audit findings and required fixes presented. A production release requires evidence closing the checklist; it is not authorized by this document.
