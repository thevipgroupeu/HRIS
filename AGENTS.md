# Repository Agent Rules

## Scope and safety

- This is THEVIPGROUP's Horilla 2.2.0 fork; preserve upstream compatibility.
- Treat every database, media directory, account, and integration as production unless an isolated test environment is explicit.
- Never run production migrations, send real mail, generate real offers, change live recruitment state, reset accounts, or delete Docker volumes.
- Never run `make clean` or `docker compose down -v` without explicit, target-specific approval.
- Use synthetic people, applications, documents, and addresses in tests and fixtures.
- Do not place secrets, candidate documents, personal data, access tokens, or live payloads in Git, logs, fixtures, docs, or prompts.

## Architecture

- Reuse Horilla `Candidate`, `Recruitment`, `Employee`, onboarding, leave, attendance, asset, permission, and document capabilities.
- Put THEVIPGROUP behavior in isolated `tvg_*` apps. Prefer OneToOne/link models, services, signals, and Horilla's inheritance hooks over upstream edits.
- Do not add replacement Candidate, Employee, Recruitment, or Leave models. Record a written architecture decision before changing their semantics.
- Keep public CareerHub URL configuration, authentication, cookies, data access, and deployment separate from private PeopleHub.
- Treat Cloudflare Tunnel as transport, not authorization. Apply least privilege and object-level authorization in Django/API code.
- Every schema change needs a Django migration. Every feature needs unit, integration, authorization, tenant-isolation, and negative-path tests.
- External side effects must be explicit, idempotent, auditable, retryable, and disabled or redirected to fakes in development/test.
- AI may assist humans but must never make or enact hiring, rejection, verification, or offer decisions.

## Development workflow

- Use Python 3.12 and `requirements.txt`; CI also uses PostgreSQL 16 and Redis 7.
- Start from a separate local/staging database. Do not reuse `.env` or media from a deployed instance.
- Inspect `git status` before and after changes; preserve unrelated user work.
- Prefer focused checks before the full suite:
  - `python manage.py check`
  - `python manage.py check --fail-level WARNING --tag horilla.tenancy`
  - `python manage.py makemigrations --check --dry-run`
  - `python manage.py test <app-or-test-label> --verbosity=2`
  - `ruff check <changed-paths>`
  - `black --check <changed-python-paths>`
  - `isort --check-only --profile black <changed-python-paths>`
- Full CI-equivalent tests use `make test-cov`; never point them at a non-test `DATABASE_URL`.
- Update architecture/security docs when a boundary, data owner, public route, authorization rule, or integration changes.

## Commits

- Keep commits small and explain migrations, security behavior, and deployment effects.
- Include `Co-Authored-By: OpenAI Codex <noreply@openai.com>` when Codex materially authored the change.
