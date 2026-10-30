# Migration plan and rollback strategy

**Planning document only. No production execution authorized.** Date: 28 October 2026.

## Evidence and release blocker

- `evidence-migrations.json`: private-runtime model consistency check passed; complete existing migration graph applied to an empty in-memory SQLite DB.
- `evidence-pg_migrations.json`: private `check`, strict `horilla.tenancy` check, `makemigrations --check --dry-run` and complete existing migration graph passed on disposable PostgreSQL 16.15. Total harness duration 921.95 seconds includes initialization/checks/migration work; it is not production downtime. A fictional Person and two distinct native Candidate/Application records were created afterward.
- **MIG-01 / release blocker:** `evidence-public_pg_migrations.json` reports a missing migration in the separate CareerHub settings: `Alter field event_type on candidatesecurityevent`. Current model includes arrangement-date confirmation and acceptance-download/receipt choices not present in migration 0005. This is model/migration-state drift; no missing physical column or existing data corruption was demonstrated. The public runtime must be checked independently of private Horilla because its account app is not installed there.
- Existing CareerHub migrations were subsequently applied only to a disposable database for restore testing; the runner preserves exit code 1 for the drift. No new application migration was generated or applied by the auditor.
- The public test suite includes a synthetic 0001-to-0004 compatibility test. This does not cover every current upgrade path, every deployed migration version, or production data shape.

## Before any staging migration

1. Release owner records the exact reviewed commit, immutable image digest, dependency lock/SBOM, Python 3.12, PostgreSQL 16 and Redis 7 patch versions. Include all reviewed `tvg_*` apps and migrations currently untracked. Do not lose unrelated user work or assume current HEAD contains the customized platform.
2. Developer adds/reviews the CareerHub event-choice migration and runs consistency checks under **both** settings. Retest current and historical event values; retain events rather than rewriting history. Do not edit already-applied migrations to silence drift.
3. DBA inventories actual migration records in each authorized target without dumping personal records. Record private Horilla and separate CareerHub DB identities, owners, storage roots, grants, extensions, migration leaf nodes and current schema version. Public accounts must never be migrated into the private employee-auth DB.
4. Build an isolated staging database pair with synthetic representative histories or an explicitly authorized, properly anonymized copy. Outbound mail/integrations must be disabled or redirected to a local sink. Use separate secrets, filesystem roots, cache and network.
5. In that isolated environment, run `check`, `check --deploy`, private `check --fail-level WARNING --tag horilla.tenancy`, `makemigrations --check --dry-run`, `showmigrations --plan` and `migrate --plan` with the correct settings. Never run these commands from an unqualified shell that may read the deployed `.env`. Store sanitized outputs only.
6. Review each pending migration's locks, row scans, unique constraints, null handling, foreign keys, data backfills and reverse behavior. Reconcile duplicate emails/Person links, application IDs, staff-ID sequences and document-version uniqueness before enabling constraints. Do not auto-merge persons by email.

## Important source-specific details

- Most TVG models link to native Horilla models rather than replace them. Upstream recruitment model/form/view modifications still require merge/upgrade regression tests, particularly verification and interview guards.
- `tvg_careers/0004` backfills questionnaire versions and declares `RunPython.noop` as the reverse. Reversing schema migrations does **not** restore the original business-state semantics or recreate lost snapshots.
- `tvg_analytics/0002_snapshot_revisions_reporting_view` creates a separate PostgreSQL reporting schema, revokes PUBLIC access and conditionally grants access to an already-created reader role. Its reverse drops the view, not the whole reporting schema. Provision the reader role using the reviewed DBA script, with no broad public-schema grants. Verify it can select only approved aggregate views and cannot select HR/account tables or write anything.
- Current web entrypoint automatically runs `migrate` and `collectstatic --clear`. CareerHub/PeopleHub have different entrypoint behavior. An approved release should use one controlled migration job per DB and start serving processes with release tasks disabled; do not let replica starts perform migrations opportunistically.
- Configure `Europe/Warsaw` and test DST/business-date semantics. `.env.dist` still defaults to `Asia/Kolkata`; review historic data assumptions before changing date interpretation.

## Staging execution and validation

1. Quiesce staging write producers and stop its scheduler/outbox consumers. Snapshot both databases and **all** private media with a common recovery marker; prove restore before proceeding.
2. Run migrations once for the private DB and once for CareerHub under their explicit settings. Retain ordered migration logs, timings and before/after row-count/invariant summaries. No `--fake` or `--fake-initial` without a separate schema reconciliation and DBA approval.
3. Run full runtime-specific tests, new negative probes, and the continuous fictional lifecycle with the second application. Test concurrent application retry, letter/version generation, staff ID allocation and outbox replay against PostgreSQL, not only SQLite.
4. Check no pending migrations, no orphan Person/Candidate/Application/Employee links, no duplicated staff IDs, unchanged approved document hashes, complete audit histories, maintained asset ownership and exact analytics reconciliation.
5. Start one scheduler, inspect queue age/failures and prove no duplicate message/provisioning side effects using fake integrations. Re-enable external production adapters only in a separately approved rollout.

## Rollback decision tree

- **Code-only, backward-compatible schema:** freeze new feature writes, stop new consumers, restore the previously approved image, run compatibility smoke checks, and retain the additive schema. Do not reverse migrations merely to match a version label.
- **Incompatible or destructive/data-changing migration:** keep writes paused. Restore both DBs and corresponding private-file snapshot to **new isolated targets**, validate hashes/constraints/links, then switch to the validated old release and restored targets with DBA/release-owner authorization. Never blindly restore over the live database.
- **Writes occurred after checkpoint:** determine lost/replayed commands and the permitted RPO before rollback. Reconcile outbox/external side effects by stable idempotency key. A database rollback cannot unsend a letter or revoke an already-delivered message. Obtain explicit authority for any business reversal.
- Preserve failed targets read-only for investigation under approved retention/access policy. Do not delete recruitment, internship, approved legal history or Docker volumes as rollback cleanup.

Release prerequisites still missing: actual upgrade rehearsal from the deployed versions, reviewed reverse plans per pending migration, PostgreSQL concurrency coverage, approved recovery checkpoint policy and operator sign-off. Empty-database migration success and sparse synthetic restore success are necessary but insufficient.
