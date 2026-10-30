# Fit-Gap Analysis

## Summary

Horilla already supplies the internal HR system of record and most operational modules. The fork should not rebuild recruitment, Candidate applications, Employee, onboarding, leave, attendance, assets, reporting or permissions. The substantial work is the safe public candidate boundary, reusable candidate identity, Erasmus verification/policy, document security, internship-specific reporting and durable integration infrastructure.

| Required capability | Existing fit | Gap / decision | Disposition |
|---|---|---|---|
| Legal organization and three business areas | Company and job/department structures exist | No stable `FluxOps`/`FindMyStay`/`THEVIPGROUP` business-area taxonomy | Extend in `tvg_core` |
| Internal ATS | Recruitment, stages, managers, surveys, Candidate, interview, rejection | No verification-gated interview policy | Reuse plus policy extension |
| Public job list | Published open recruitment page | Shares private URL surface; unpublished detail fragment is addressable | Replace public surface; patch filter |
| Public application | Form, CV, survey, initial stage | Verified-account draft, immutable snapshot and secure resumption were absent | Implemented in `tvg_careers`; idempotent private command submits to existing Horilla Candidate/Recruitment models |
| Configurable application questionnaire | SurveyTemplate, RecruitmentSurvey, Recruitment template assignment and RecruitmentSurveyAnswer | Native configuration was mutable and used question text as answer keys; no public schema/version provenance | Reuse native authoring/answers; immutable TVG version and submission snapshots implemented per ADR-005 |
| One identity, multiple applications | Multiple Candidate rows are allowed across recruitments | Candidate is application-shaped; login selects first match | Add candidate account/profile and mapping; keep Candidate |
| Secure candidate portal | Basic status/interview tracking | Email+phone credential, no explicit throttling/MFA/email verification, orphan logout view | Replace, do not extend legacy login |
| Candidate document requests | CandidateDocumentRequest/Document and approval state | No scan/quarantine/version/retention; central media authorization too broad | Reuse records; build security/verification layer |
| Configurable Erasmus verification | Candidate docs can be requested | Production scanner/delivery scheduling and DPIA approval remain | Implemented in `tvg_erasmus`; operationalize before release |
| Block interview until approved | InterviewSchedule exists | No invariant across UI/API/admin/automation | Central policy/command gate plus tests |
| Candidate interview booking and decisions | InterviewSchedule and Stage exist | No slot choice, timezone interval, provider boundary, conflict/reschedule policy, private structured feedback or approved rejection template | Reuse native records; `tvg_interviews` adds the workflow and human-decision controls per ADR-007 |
| Configurable university requirements | Surveys and generic document requests | No program/university/version/effective-date model | Build configuration, never hard-code NA10 |
| Candidate-to-employee conversion | Existing conversion and onboarding proxy/tasks | Legacy portal token/state weaknesses | Reuse conversion/tasks; harden token workflow |
| Staff HR record | Employee/User/WorkInformation | No TVG-specific stable external/business-area metadata | Reuse; small OneToOne metadata only |
| Leave and attendance | Mature models/workflows and scheduled jobs | Configuration/local policy still required | Configure/reuse |
| Asset issue/return | Assets, assignments, requests, service | Signed acknowledgement/return evidence may be missing | Reuse; extend only confirmed requirement |
| Employee documents | Request, approval, issue/expiry/notification | Object-level media access and retention require hardening | Reuse plus `tvg_documents` |
| Weekly/monthly intern reports | Generic report templates/subscriptions exist | No intern submission/reviewer workflow | Build `tvg_reports`, call existing reporting services |
| Zoho Mail | Dynamic SMTP backend | Credentials stored plaintext; no dev delivery guard by default | Adapter/secrets hardening and environment guard |
| Activepieces | No durable outbound event integration | In-process mail threads are lossy and not idempotent | Build outbox in `tvg_automation` |
| Moodle | No explicit integration | Need identity/enrollment/progress mappings and reconciliation | Build adapter in `tvg_integrations` |
| authentik staff SSO | Staff auth, optional LDAP and 2FA exist | No authentik/OIDC lifecycle integration | Future adapter; retain Horilla authorization |
| Metabase | Existing reports only | No curated least-privilege analytics role/views | Curated views plus adapter/deployment |
| Cloudflare Tunnel | No service/config in Compose | Network entry and PeopleHub access policy not represented | Deployment work; never sole auth layer |
| REST/automation API | Broad JWT API exists | Detail object permission gaps; broad `__all__` serializers | Patch and add purpose-built integration API |
| Audit trail | Defaults cover employee/payroll/leave/attendance; some simple history | Recruitment/verification/document/interview coverage inconsistent | Extend append-only auditing and alerts |
| AI assistance | Some resume-ranking code exists | Governance/provenance/human decision boundary not formalized | Advisory only; no automatic status/offer decisions |

## Reuse rules by model

### Candidate

Keep `recruitment.Candidate` as a submitted application. Its current uniqueness and foreign keys are consistent with that meaning. A new candidate account/profile is justified because it is an authentication identity spanning applications, not another ATS Candidate.

The link should use opaque stable identifiers and support one account to many Candidates. Do not merge historical Candidate rows or change their email/recruitment uniqueness in the first implementation phase.

### Recruitment and Stage

Keep Horilla as owner. Attach public copy, venture classification, locale and publication metadata through an extension record if fields do not naturally belong upstream. Preserve Horilla's stage types and enforce TVG prerequisites in a service called by every stage/interview command.

### Employee and onboarding

Keep Horilla Employee/User/WorkInformation and existing candidate conversion. Add only TVG metadata, external integration mappings and policy services. Onboarding Candidate is already a proxy of Candidate, so another onboarding-person table would be duplication.

### Leave, attendance and assets

Configure and integrate existing records. New tables are justified only for a requirement Horilla cannot represent, such as a signed asset acknowledgement, and must link to the original record rather than mirror it.

### Documents

Keep the file owner records in CandidateDocument and `horilla_documents.Document`. Add verification/version/security records that reference those owners. Avoid a generic foreign key for authorization-critical ownership; use explicit nullable links with a database constraint or separate typed link tables.

## Cross-cutting gaps that block feature delivery

1. **Public/private isolation:** the current root URL set includes admin, staff apps, public recruitment and APIs. CareerHub cannot safely launch until it has a public-only deployable surface.
2. **Identity and account recovery:** email+phone must be replaced with verified, hashed credentials or a suitable identity provider, with throttling, reset, session rotation and audit.
3. **Purpose-built authorization:** tenant scoping alone does not grant permission. API details, media downloads and service commands require model/object/field checks.
4. **Durable side effects:** external workflow events need an outbox, delivery history, signatures, idempotency and dead-letter handling.
5. **Document lifecycle:** private delivery, scanning, retention and erasure are prerequisites for identity/university documents.
6. **Auditable policy engine:** verification and interview transitions must be enforced centrally with explicit human decisions/overrides.
7. **Deployment separation:** direct web port exposure, shared cookies/settings and default timezone must be corrected before production rollout.
8. **Test baseline:** the target Python 3.12 environment and PostgreSQL test suite must be reproducible locally/staging before migrations are authored.

## Upgrade-compatibility strategy

- Add top-level `tvg_*` apps through client settings rather than renaming or moving Horilla apps.
- Prefer OneToOne/foreign-key extension models and service adapters. Use `_inherit` only where a real upstream field/CBV extension is required and covered by migration tests.
- Keep security fixes to upstream files small, independently tested and suitable for upstream contribution.
- Maintain an upstream remote, record the upstream base commit for each merge and avoid mixing upstream synchronization with TVG feature commits.
- Contract-test the narrow CareerHub and automation APIs so internal Horilla serializers can evolve without breaking public clients.
