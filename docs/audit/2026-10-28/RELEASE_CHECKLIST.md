# Production release checklist

Date: 28 October 2026. **Release not approved.** A checked item below is limited to its stated scope. All unchecked release gates need evidence and a named approver. Critical authorization/data-integrity failures cannot be waved through by a general risk acceptance.

## Audit evidence completed

- [x] Repository/architecture review covers all 18 requested areas and preserves existing work.
- [x] 239 private TVG tests, 37 independent public account tests and 51 selected native access/tenancy tests pass locally.
- [x] Four failing authorization probes and three failing configuration checks recorded with synthetic data.
- [x] Current dependency/upstream review completed; installed-package advisory metadata recorded.
- [x] Private migration drift check, tenancy check and empty PostgreSQL migration graph pass.
- [x] Separate public migration drift detected and documented, not silently fixed.
- [x] Both sparse synthetic application databases restore with matching rows/views/constraints/identity sequences.
- [x] Eight synthetic document roots restore with matching hashes.
- [x] Small analytics benchmark reconciles 50 people versus 100 applications; scope limitations recorded.
- [x] Security/functional reports, migration/recovery plans, deployment/rollback runbook and unresolved risks produced.

## Mandatory security and integrity gates

- [ ] SEC-01/02/03 regression tests pass: private media and Candidate detail deny unrelated users; foreign-employer application cannot be read/withdrawn.
- [ ] Same-company, cross-company, anonymous, applicant, employee, supervisor, HR, IT and disabled-user matrix passes at actual proxy/API/UI surfaces.
- [ ] Production DEBUG/weak-secret/wildcard guards fail closed; all runtime `check --deploy` results reviewed.
- [ ] Fixed dependencies installed in a reviewed immutable image; residual no-fix advisories have specific mitigations, owner and expiry.
- [ ] Full-history and artifact secret scan reviewed; historical exposed credentials rotated/revoked with proof; independent environment credentials installed through approved management.
- [ ] Token/path/referrer/log redaction verified at application, Gunicorn, nginx, edge and monitoring layers.
- [ ] Production scanner/quarantine/timeout gates validated; fake scanners/providers cannot accidentally approve real files.
- [ ] Public DB/principal/media/session separation and database grants tested; private URLs absent from CareerHub; no direct-origin bypass.
- [ ] CareerHub missing migration added and migration checks run independently for both runtimes.
- [ ] Existing agreements, approvals, signatures, document versions and identity histories survive upgrade/rollback rehearsals unchanged.

## Functional end-to-end acceptance

- [ ] One fictional account registers and verifies through real staging bridges with mail captured locally.
- [ ] Same Person submits FluxOps and FindMyStay applications with independent snapshots/questionnaires/CVs and unique stable application IDs.
- [ ] FluxOps screening -> requested documents -> scanned evidence -> human verification -> scheduling -> human selection succeeds without direct database state shortcuts.
- [ ] Candidate confirms dates; minimum-duration policy/authorized exceptions honored; approved arrangements/template precede human letter issue/send.
- [ ] Learning Agreement review/curriculum/signatures/return/remaining signatures, mandatory legal documents and university-specific forms complete before onboarding.
- [ ] Onboarding creates exactly one Employee/Intern identity, unique staff ID, approved permissions/training and native asset allocation/acknowledgment.
- [ ] Weekly/monthly reports receive authorized supervisor feedback with immutable revision history.
- [ ] Completion certificate uses approved template; After Mobility, asset return/signatures, access-revocation proof and administrative closure satisfy every mandatory gate.
- [ ] Disabled intern cannot log in or reuse sessions/tokens; external/manual deactivation proof is reviewed; historical legal/recruitment records remain.
- [ ] FindMyStay application remains independent and is not implicitly selected/withdrawn by FluxOps workflow.

## Infrastructure, recovery and integration acceptance

- [ ] Cloudflare host routes/catch-all/Access, trusted HTTPS/client IP, secure cookies, CSRF, rate limits and no-loop behavior tested through the actual staging proxy.
- [ ] Staff-only full PeopleOps route decided and tested; employee/public links resolve under correct host without exposing HR administration.
- [ ] Separate DB migration jobs and versioned static assets replace uncontrolled startup migration/clear races.
- [ ] Complete coordinated backups include both DBs and every private root, encrypted/off-site with tested key recovery and restore ACLs.
- [ ] Representative clean-host restore proves approved RPO <= 1h / RTO <= 4h, or owners approve revised targets before release.
- [ ] Exactly one scheduler/leader; crash/retry/missed job/duplicate execution tests and alerts pass, including native backup exception.
- [ ] First three Activepieces staging automations run with fake delivery; signed callbacks, replay, duplicates, cancellation and dead-letter recovery verified on target CE version.
- [ ] Mail From, Reply-To, approved templates, TLS/auth, domain authentication and bounce handling accepted using authorized test sink; no real test messages.
- [ ] Signing, scheduling, Moodle/authentik and provisioning/deprovisioning adapters individually approved or deliberately left disabled with tracked manual fallback.
- [ ] Monitoring ownership, alert delivery/escalation and daily HR exception queue verified.

## Privacy, usability, performance and sign-off

- [ ] Privacy owner approves notices, minimization, university-specific document requirements, retention/holds and DSR handling across databases/files/backups/processors.
- [ ] No nationality/protected-characteristic automated hiring decision; analytics exports remain aggregate/minimized and Metabase read-only grants tested.
- [ ] Accessibility scans plus keyboard/screen-reader/mobile/zoom walkthroughs pass; no serious blocker on core candidate/staff workflows.
- [ ] Representative 500 applications/day, 50-user concurrency and 10,000-application load test meets approved response/error/queue targets; overload behavior validated.
- [ ] Full Python 3.12/PostgreSQL 16/Redis 7 CI, runtime-specific suites, concurrency tests and joined E2E gate the exact release artifact.
- [ ] Release owner, security reviewer, DBA, infrastructure operator and HR/privacy approver sign the evidence pack and rollback checkpoint.
- [ ] Separate explicit deployment authorization obtained. This audit request is not deployment authorization.
