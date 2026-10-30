# Existing System Audit

**Audit date:** 2026-10-25

**Scope:** source repository and supplied PeopleHub/CareerHub requirements only

**Method:** read-only source/configuration inspection plus non-starting Compose validation

This document describes the checked-out source, not a live deployment. No database was opened, no container was started, no migration was applied, and no mail or external integration was invoked.

## Repository identity

| Item | Observed value |
|---|---|
| Branch | `2.0`, tracking `origin/2.0` |
| Commit | `9c97e785f505a6dc2feae2312609ee28f32993b0` |
| Commit subject/date | `[REL] Add the security section to the 2.2.0 changelog`, 2026-10-21 |
| Source version | Horilla `2.2.0`, API contract version `1` (`horilla/__version__.py`) |
| Remotes | Only `origin`, `https://github.com/thevipgroupeu/HRIS.git` |
| Upstream remote | Not configured |
| Tags | No local tags; HEAD is not tagged |
| Worktree at audit start | Clean |
| Language/runtime | Django 5.2 project, repository/CI target Python 3.12 |

The branch name, missing upstream remote, and absent local tags make upstream-release comparison harder than it needs to be. Establishing an authoritative upstream remote and release-tag policy is a repository-governance prerequisite, not an application feature.

## Application and settings structure

The repository is a large Django modular monolith. `horilla/settings/__init__.py` loads `base.py`, then `addons.py`, then tracked client overrides in `local_settings.py`. The root URL configuration is `horilla/urls.py`; apps such as recruitment append their URL includes at `AppConfig.ready()` time (`recruitment/apps.py:31`). There is no hostname-specific URL configuration.

Core installed capabilities include recruitment, onboarding, employees, attendance, leave, assets, payroll, reports, employee documents, audit, automation, REST API, LDAP, meeting/outlook/WhatsApp/biometric integrations, and Django admin. No `tvg_*` app exists and all seven proposed app labels are free.

Important defaults and controls:

- `AUTH_USER_MODEL` is Horilla's staff user model. Staff authentication uses django-axes first and a company-scoped permission backend (`horilla/settings/base.py:618`). Two-factor support exists for staff.
- DRF defaults to tenant-scoped JWT authentication and `IsAuthenticated`. Access tokens last 60 minutes; rotating refresh tokens last 30 days and are blacklisted on rotation.
- Company scoping is request/context based. `HorillaCompanyManager` returns an unfiltered queryset when it cannot resolve a selected company (`base/horilla_company_manager.py:58` onward); API authentication rejects an unplaceable non-superuser, but background and public code must deliberately choose a safe manager.
- File storage defaults to local filesystem. Optional S3 storage is enabled through `horilla/settings/addons.py`. Static files use WhiteNoise.
- The default timezone is `Asia/Kolkata`, not the target organization's `Europe/Warsaw`.
- CORS middleware is installed, but no allow-list setting is defined in the checked-in configuration; django-cors-headers therefore remains deny-by-default unless deployment overrides it.

Horilla includes model and class-based-view extension hooks in `horilla/inherit/`. They can inject nullable/defaulted fields and extend registered CBVs while routing migrations to the extension app. They are useful selectively, but ordinary OneToOne extension models and service boundaries remain less coupled for most TVG data.

## Existing domain capabilities

| Area | Existing implementation | Assessment for PeopleOps |
|---|---|---|
| Recruitment | `Recruitment`, stages, managers, vacancies, job positions, surveys, candidates, interviews, rejection, talent pools | Strong internal ATS foundation; reuse |
| Candidate | `Candidate` contains one application, contact data, CV, stage and conversion link | Application record, not a reusable candidate identity |
| Public jobs | Published open-job list, job fragment and public application form | Useful foundation; not an isolated CareerHub |
| Candidate tracking | Session portal authenticated by email plus phone | Inadequate for secure multi-application accounts |
| Candidate documents | Requests, upload, requested/approved/rejected state | Reuse and extend; verification policy/gate is missing |
| Onboarding | Per-recruitment stages/tasks, candidate task state, token portal, employee conversion | Strong workflow base; token flow needs hardening |
| Employees | Staff identity, work information, manager, company, department, job, location, contract data | System of record; reuse |
| Leave | Policies/types, balances, requests, approvals and allocations | Reuse |
| Attendance | Check-in/out, validations, requests, work records and scheduled maintenance | Reuse |
| Assets | Categories, items, assignments/returns, requests and service records | Reuse; add acknowledgements only if required |
| Employee documents | Requests, upload, approval, expiry and notification | Reuse through `horilla_documents` |
| Reporting | Templates, subscriptions, saved views, access rules and run logs | Reuse; add only internship-specific reports |
| Automation | Conditional model-event email/notification automation | Useful locally, not a durable integration bus |
| REST API | Versioned `/api/v1/`, legacy `/api/`, JWT, filters and Swagger schema | Broad API exists; narrow/secure it before integrations |

### Recruitment and Candidate data model

`Recruitment` is company-scoped and owns open positions, stages, managers and survey templates (`recruitment/models.py:159`). `Stage` belongs to a recruitment and has applied/test/interview/cancelled/hired types (`recruitment/models.py:443`).

`Candidate` (`recruitment/models.py:624`) points to one recruitment, job position and stage, holds the CV and contact information, and optionally links to a converted `Employee`. Its unique constraint is `(email, recruitment_id)` (`recruitment/models.py:1419`). Consequently:

- one person applying to three recruitments becomes three Candidate rows;
- profile data and CV are copied per application;
- login cannot naturally represent all applications for one person;
- the model should remain the Horilla application-of-record rather than be replaced.

Survey answers link to Candidate and can link to recruitment/job, but the public survey flow sets the candidate and answer JSON without visibly setting those two nullable fields. Answers are keyed by question text, making renamed/repeated questions difficult to version. `InterviewSchedule` has no model/service constraint tying interview creation to document verification (`recruitment/models.py:1822`).

### Public job and application flow

The following routes are on the same URL tree as HR, API and Django admin:

- `/recruitment/open-recruitments/` uses the unscoped manager but correctly filters `closed=False`, `is_published=True`, `is_active=True` (`recruitment/views/views.py:3170`).
- `/recruitment/recruitment-details/<id>/` uses the unscoped manager and filters only by id (`recruitment/views/views.py:3186`), allowing an unpublished recruitment's fragment to be requested if its numeric id is known.
- `/recruitment/application-form/` validates that the chosen recruitment is published and creates the Candidate in the initial/applied stage (`recruitment/views/surveys.py:569`). The unique constraint allows only one application per email per recruitment.
- `/recruitment/candidate-survey/` continues from a serialized Candidate in the session and permits attachments up to 5 MB (`recruitment/views/surveys.py:157`).

There is no public-account registration, email verification, password reset, reusable profile, consent ledger, draft application, immutable submission snapshot, or multi-application dashboard.

### Candidate and onboarding authentication

`CandidateAuthenticationBackend` compares email and normalized phone digits, accepting either an exact number or matching last ten digits (`recruitment/auth.py`). The first Candidate row that matches becomes a session containing `candidate_id` and email. This is neither Django authentication nor a durable candidate identity. The route has no explicit django-axes integration or application-level throttle. `candidate_logout()` exists, but no logout URL is registered in `recruitment/urls.py`.

The onboarding portal uses a 30-hex-character bearer token stored in plaintext with `used` and `count`, but no issue/expiry timestamps (`onboarding/models.py:273`). Issuing a portal replaces the token. Several public steps query by token alone. Pending user state is held in the process-global `portal_user` dictionary, so it is not reliable across workers/restarts (`onboarding/views.py:1130` onward). These mechanisms must not be reused as CareerHub authentication.

### Documents and media access

Candidate document object access in the HTML views has explicit session scoping and regression commentary/tests for previous IDOR fixes. Extension and size checks exist, and active browser content is forced to download by the central media view. Remaining limitations are material:

- `protected_media()` allows any authenticated staff session or any valid JWT to retrieve any non-public file path; it does not authorize the referenced business object (`base/views.py:8847`).
- candidate profile images are intentionally public by path prefix.
- candidate `view_file()` uses `.path` and Python `open()`, which is incompatible with remote storage backends such as S3 (`recruitment/views/views.py:4372`).
- validation is mainly filename-extension/size based; no malware scanning, content-signature validation, quarantine, retention or erasure workflow exists.

### API and authorization

Tenant-scoped JWT establishes a company ContextVar and refuses non-superusers without a resolvable company (`horilla_api/authentication.py:86`). List endpoints generally apply model permissions or employee/manager narrowing. This is a good tenant boundary, but it is not sufficient object authorization.

Recruitment detail GETs call `object_check(Model, pk)` and require only authentication. Candidate and CandidateDocument detail endpoints therefore allow an authenticated employee in the same selected company to fetch a known numeric id without the corresponding `view_*` permission (`horilla_api/api_views/recruitment/views.py:254`, `:316`, `:1106`, `:1161`). `CandidateSerializer` and document serializers use `fields = "__all__"` (`horilla_api/api_serializers/recruitment/serializers.py:239`), increasing disclosure and write-target risk. These broad serializers must not be exposed to CareerHub or Activepieces.

### Automation and integrations

`horilla_automations` evaluates conditions on model changes and sends mail/notifications. Work starts after transaction commit but runs in in-process Python threads (`horilla_automations/signals.py:138`, `:217`, `:604`). There is no durable outbox, delivery state, retry/backoff, webhook signing or idempotency contract.

SMTP settings can support Zoho Mail, but `DynamicEmailConfiguration.password` is a normal database `CharField` (`base/models.py:2062`). No explicit Activepieces, Moodle, authentik, Metabase or Cloudflare integration/configuration was found. Redis currently supports caching, not a task queue.

### Audit coverage

The default django-auditlog set covers employee identity/work/bank data, payroll, leave and attendance (`horilla_audit/registry.py`). Recruitment, onboarding, candidate documents, interviews and assets are not in the default set. Some recruitment records use simple-history, but coverage is inconsistent. If any `AuditModelConfig` rows exist, they replace the defaults wholesale, which can accidentally reduce the audit floor.

## Deployment and background work

Development Compose defines web, PostgreSQL 16, Redis 7, a single scheduler and optional nginx. The image runs as a non-root user. Web entrypoint runs migrations and collectstatic; the scheduler skips those release tasks.

Production overlay requires secrets from `.env`, applies production security validation, persists media/static volumes and declares one scheduler replica. Important gaps:

- web remains directly published on host port `8000` (`docker-compose.prod.yml:31`), so traffic can bypass nginx/Cloudflare routing unless the host firewall closes it;
- there is one web route set for both public and private functionality;
- the stack does not include Activepieces, Moodle, authentik, Metabase or a Cloudflare connector;
- the single-replica scheduler guarantee depends on the orchestrator. Its own comments state jobs are not idempotent.

`manage.py run_scheduler` uses a blocking APScheduler with a persistent Django job store, `max_instances=1` and coalescing. Jobs include recruitment conversion/closure, attendance work records, leave resets, asset expiry, payroll, reports and other maintenance. This is adequate for periodic Horilla jobs but not a substitute for a durable event integration boundary.

## Tests and CI

There are 187 Python test files and 284 migration Python files. Coverage is uneven: leave, attendance and API security have meaningful suites; assets and onboarding have much smaller coverage. Existing recruitment tests include prior candidate/document authorization regressions. There are no tests for the not-yet-existing TVG account, Erasmus, verification-gate or external-integration flows.

CI includes:

- Python 3.12/PostgreSQL 16 smoke tests, migration-drift check and branch coverage (`make test-cov`);
- a recorded 27.6% branch coverage with a 26% floor;
- whole-repository Ruff bug rules, scoped Black/isort and Django/tenancy checks;
- dependency audit, new-commit secret scanning and production-settings rejection;
- Docker build/migrate/collectstatic/health/readiness and Trivy scanning;
- CodeQL analysis and image publishing workflow.

The secret-scanning workflow documents historical AWS/WhatsApp credentials and 161 historical findings while scanning only newly added commits. Rotation/history review is therefore an operational requirement even though no live secret was found by the narrow working-tree grep in this audit.

## Audit limitations

The local machine has Python 3.13.14, no virtual environment, no Django installation, no Ruff installation and no `TestDB.sqlite3`. Django checks and tests could not be executed without installing a new environment. Docker/Compose are installed, and `docker compose config --quiet` succeeded without starting services. CI results and the state of deployed infrastructure, DNS, tunnels, databases, object storage and secrets were not independently verified.
