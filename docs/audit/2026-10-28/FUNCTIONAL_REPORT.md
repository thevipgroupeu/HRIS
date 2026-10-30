# Functional test report

Date: 28 October 2026. **Component tests pass; the requested complete cross-runtime journey is NOT accepted.** All people/files/messages used by the tests were fictional and ephemeral. No test candidate was inserted into a deployed system.

## Executed tests

| Suite | Count / outcome | Runtime evidence |
|---|---|---|
| TVG private apps: core, careers, Erasmus, interviews, onboarding, PeopleHub, reporting, offboarding, automation, training, analytics | 239 passed; test body 126.095 s | `evidence-private.json` |
| Separate CareerHub accounts/portal | 37 passed; test body 4.130 s | `evidence-public.json` |
| Selected native access/tenancy regression tests | 51 passed; test body 7.486 s | `evidence-native.json` |
| Additional authorization probes | 4 failed | `evidence-probes.json` |
| Additional configuration checks | 3 failed | `evidence-configuration.json` |

Tests ran against local isolated settings, not a deployed staging stack. Public and private test runs use different Django app registries. Local-memory mail and synthetic/disabled provider adapters replace external effects. Password hashing is deliberately accelerated in the test harness; timing is not representative of production login.

## Requested fictional lifecycle: evidence and gaps

This matrix describes executed **separate** scenarios. It is not a claim that one persistent candidate traversed every row.

| Requested step | Fresh component evidence | Limitation |
|---|---|---|
| Register; verify email | `CandidateRegistrationTests`, `CandidateVerificationTests` | Verification email held in memory; identity service is mocked in the public suite. |
| Apply to FluxOps; questionnaire; CV | `CareerHubApplicationAPITests`, candidate portal snapshot tests | Real private API handlers and synthetic PDF/DOCX; public delivery client mocked in its separate suite. |
| Same identity applies to FindMyStay | `test_one_person_applies_to_two_vacancies_with_independent_horilla_records`; `test_one_account_can_hold_independent_applications_to_two_ventures` | Confirms one identity / separate applications and native Candidates within each boundary; not live network handoff. |
| HR screen; request evidence; HR verify | `VerificationWorkflowTests` | Tests real service gates and revisions with fake clean scanner; no production scanner. |
| Interview scheduled; human selects | Interview workflow suite; acceptance selection helper | Simulated scheduling provider. Decision is a human-authorized service command, not AI. |
| Dates confirmed; acceptance letter issued | `test_candidate_completes_acceptance_workflow_through_private_api`; duration/conflict/approval/version tests | Synthetic approved template and fake/local mail only. No assertion that real company legal fields/signatures are approved. |
| Learning Agreement; legal documents | `test_synthetic_candidate_completes_learning_agreement_lifecycle`; company-document, state/signature/version tests | Manual synthetic signed uploads; individual document scenarios rather than every mandatory legal document in one combined case. |
| Onboarding; intern ID; assigned asset | `test_fake_accepted_candidate_becomes_onboarded_intern`; duplicate-ID/access/allocation tests | Pre-start completion is shortcut in setup (below). |
| Weekly report; monthly presentation; supervisor feedback | `test_fake_intern_completes_weekly_monthly_and_receives_feedback`; PeopleHub form test | Synthetic PDF and approved rubric; separate internship fixture. |
| Training assigned and progress | `test_authorized_staff_enrollment_projects_progress_to_peoplehub`; role/session tests | Synthetic Moodle provider; actual enrollment/SSO/logout not exercised. |
| Completion; certificate; asset returned | `test_full_synthetic_internship_offboarding_flow` | Real completion service path after an already-onboarded setup fixture. |
| Company access revoked; After Mobility closed | Same completion flow, access-proof and optional-form tests | Manual synthetic proof records; no real Zoho/GitHub/VPN/SSO deactivation. |

### Why the joined journey is not a pass

The acceptance helper sets verification requirements/case directly to `VERIFIED` ([test_acceptance_workflow.py:178](C:/Users/DELL/Desktop/HRIS/tvg_erasmus/tests/test_acceptance_workflow.py:178)). Onboarding setup sets `before_mobility_completed_at` directly ([test_intern_onboarding.py:108](C:/Users/DELL/Desktop/HRIS/tvg_onboarding/tests/test_intern_onboarding.py:108)). Offboarding setup sets `ONBOARDED` directly ([test_completion_workflow.py:160](C:/Users/DELL/Desktop/HRIS/tvg_offboarding/tests/test_completion_workflow.py:160)). These are legitimate unit-fixture shortcuts but cannot prove the complete real transition chain. Public identity/application bridges are mocked separately. No continuous browser/two-database/scheduler/mail-sink scenario was completed in this audit. Confirmed security failures independently block acceptance.

Required acceptance rerun: isolated staging stack with fictional `Audit Candidate <unique suffix>` at an owned test sink or reserved `.invalid` address; create FluxOps and FindMyStay openings/questionnaires; submit both via CareerHub; persist one Person and two application identities; complete the FluxOps chain through actual UI/API transitions with distinct candidate, recruiter, supervisor, HR approver and signer actors. Do not write completed-state flags directly. Use watermarked synthetic approved templates, fake external adapters, local mail capture, explicit simulated signature evidence, and recorded manual IT proof. Confirm the FindMyStay application is retained and not implicitly hired/withdrawn. Assert record counts, all audit events, current document hashes/versions, asset borrower/return, training association, disabled employee access and retained legal history after closure. Capture each step and denial checks in CI.

## Routing and usability observations

Source route paths are PeopleHub `/login/` and CareerHub `/candidate/create-account/` / `/candidate/sign-in/`. Hostnames configured in source are `people.thevipgroup.eu` and `career.thevipgroup.eu`. These are **configuration references, not verified working live links**. The supplied nginx points the employee hostname to the restricted PeopleHub URLconf, so HR routes there returning 404 are expected. It defines a `web` upstream but no server routing to full PeopleOps; design an authenticated staff-only HR entry point instead of exposing HR routes in CareerHub/employee navigation. Do not solve 404s by mounting unrestricted HR URLs publicly.

The HTTP-only nginx overwrites forwarded scheme with its local `$scheme`, conflicting with the supplied secure redirect defaults behind an HTTPS-terminating tunnel. Actual Cloudflare DNS/tunnel/Access configuration was not available for live validation. See the runbook.

## Accessibility/mobile

Source review found viewport metadata, semantic `main`/navigation, labeled Django forms, a CareerHub skip link/mobile menu and responsive styles. PeopleHub tables, long filenames, error focus/announcements, contrast, keyboard ordering and overflow still need rendered testing. No WCAG conformance or browser/mobile pass is claimed. Test at 320, 375, 768 and 1280 CSS pixels, 200/400% zoom, keyboard-only operation and at least one screen reader. Cover signup, verification, upload/revision feedback, date confirmation, report submission and HR approval. Add automated accessibility scans and fix serious/critical findings, then manually verify semantics and focus.

## Performance

`evidence-benchmark.json`: 50 synthetic people, 100 applications split between FluxOps and FindMyStay. Ten warmed single-process SQLite analytics collections: median **54.36 ms**, maximum **81.44 ms**, **64 SQL queries** each; reconciliation confirms 100 applications != 50 unique persons. This measures query construction/execution on a tiny local dataset, not HTTP p95, concurrency, upload performance, real password hashing, PostgreSQL contention or capacity.

Use the provisional workload in README for staging. Measure recruitment list/detail, candidate submission with concurrent duplicate attempts, approvals, downloads, portal dashboard and analytics snapshots; include scheduler/outbox work. Record p50/p95/p99, query counts, error rates, CPU/RAM, DB locks/connections and queue age. Reconcile unique IDs and idempotency after retries. Until that evidence exists, expected-volume performance is unverified.

## CI gap

The default `make test-cov` labels and coverage sources omit all `tvg_*` apps. The separate `test-tvg` uses SQLite with migrations disabled and is not called by the default unit-test job. The CareerHub account runtime needs its own explicit job. Add Python 3.12/PostgreSQL 16/Redis 7 jobs, both URL/settings surfaces, these negative probes, upgrade/rollback rehearsals and the joined synthetic journey. Do not interpret the native 26% coverage floor as TVG workflow coverage.
