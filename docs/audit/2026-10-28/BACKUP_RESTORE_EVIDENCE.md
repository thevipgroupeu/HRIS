# Backup/restore evidence and required recovery procedure

Date: 28 October 2026. **Synthetic restoration passed; production recovery is NOT certified.**

## Isolation

All PostgreSQL work used a newly created container named `tvg-audit-pg-20261028`, label `purpose=synthetic-readiness-audit`, image `postgres:16-alpine` reporting PostgreSQL 16.15. It bound only loopback port 55479 and used tmpfs for its data directory, with no existing data volumes. Fixed credentials in the scripts are explicitly synthetic and valid only for this disposable test container. Scripts check its label/environment before use. No deployed `.env`, database, media, SMTP service or candidate account was used.

After the checks, this specifically labeled disposable container was stopped and its temporary databases/dumps removed by its `--rm` lifecycle. The container is no longer present. No persistent user volume was deleted. Those synthetic contents are not retained, but can be regenerated with the audit scripts in a new isolated container; JSON evidence and reports remain. The optional temporary audit-tool virtual environment was left in the OS temporary directory because its cleanup command was blocked; it contains tooling, not production credentials or candidate records.

## Executed evidence

### 1. PostgreSQL dump/restore toolchain smoke test

`backup_drill.py` created two small relational tables, one fictional person and two applications (FluxOps/FindMyStay), used `pg_dump -Fc`, created a new target database, and restored with `pg_restore --exit-on-error --no-owner`. Ordered joined rows matched. Result: `evidence-backup.json`. This also contains the file check below; combined elapsed time was 4.92 seconds.

### 2. Full migrated application schemas with sparse synthetic data

Private migrations were applied to `tvg_audit_django`; existing public migrations to `tvg_audit_careerhub`. The public consistency check remained failed because of missing event-choice migration state; applying its existing graph for this disposable restore does not approve that drift.

`restore_application_drill.py` separately dumped both databases in custom format, created fresh `_restored_v2` targets, restored without ignored errors, and compared every user table's row count plus ordered row-data hash, view definitions, constraints and sequence state (including identity-column sequences). No row payloads were persisted into the evidence report.

| Source | Tables | Sequences | Data/views/constraints/sequences | Elapsed dump/restore/comparison |
|---|---:|---:|---|---:|
| Synthetic PeopleOps | 528 | 520 | Exact match | 19.95 s |
| Synthetic CareerHub | 12 | 8 | Exact match | 2.64 s |

Evidence: `evidence-application-restore.json`. PeopleOps contains a fictional Person linked to two native Candidate/Application records; CareerHub contains one fictional account. These fixtures are not a linked registered-to-offboarded journey and do not model production volume. The dump/restore targets were new databases, never overwrites of live databases.

### 3. Private document roots

For each root below, the drill generated an obviously synthetic PDF in a temporary source directory, archived it, extracted into a separate temporary destination and compared SHA-256 hashes and inventory equality. All eight matched:

| Storage root | Data normally held there |
|---|---|
| `media` | Native Horilla uploads, including recruitment/employee/asset files |
| `careerhub-private-media` | Candidate uploads and application snapshots |
| `verification-private-media` | Verification evidence |
| `acceptance-private-media` | Acceptance-letter versions |
| `erasmus-document-private-media` | Learning Agreement/company/university document versions |
| `onboarding-private-media` | Asset acknowledgments/onboarding documents |
| `reporting-private-media` | Monthly presentations/reporting evidence |
| `offboarding-private-media` | Certificates/return/completion documents |

Evidence: `evidence-backup.json`. This tests byte preservation and complete synthetic inventory, not deployed file permissions, malware/quarantine metadata, encrypted object storage, object version retention or legal hold behavior.

## Source-confirmed production gaps

- `horilla_backup/scheduler.py:google_drive_backup` backs up only `settings.DATABASES['default']` and `settings.MEDIA_ROOT`. It does not enumerate the separate CareerHub DB or the TVG private roots.
- Production scheduler Compose mounts native `media`, reporting and offboarding volumes, but not all other private roots. Mounting more storage alone would not make the current backup routine include it.
- Native backup scheduling remains a per-process scheduler exception, with fixed temporary filenames. Concurrent execution risks collisions; the central scheduler does not by itself correct this.
- No verified deployed schedule, off-site encryption/key recovery, access policy, monitored backup freshness, paired DB/file recovery marker, PITR/WAL configuration, or restore-on-a-clean-host evidence was available.
- Proposed RPO <= 1 hour / RTO <= 4 hours is not met merely by a daily dump. The short synthetic timings are not an estimate of production recovery time.

## Required production backup design (for review, not executed)

1. Inventory every authoritative DB and storage root, including quarantine/versioned files, approved templates, audit/outbox records and provider reference metadata. Keep secret backups in a separately controlled secret-management process; do not place secrets inside general media archives or Git.
2. Choose an approved consistency strategy: quiesce write producers and take a coordinated pair of logical DB dumps plus immutable file snapshot, or use properly coordinated PITR/object version markers. Separate PostgreSQL dumps are transactionally consistent individually, not automatically one atomic snapshot across databases and files. [PostgreSQL logical backup documentation](https://www.postgresql.org/docs/16/backup-dump.html).
3. Include role/grant/extension/schema definitions with explicit review, encryption in transit/at rest, off-site copies, immutable backup protection where appropriate, rotation/retention and a tested key recovery process. A database dump is not a backup of external files or all cluster roles.
4. Use independently named backup generations with a manifest containing release digest, DB migration leaves, recovery point, storage inventories and hashes, encryption key reference (not key), size and checksum. Alert on missed/failed/stale backups and inventory mismatch.
5. To recover, keep network egress/mail/jobs disabled; create new isolated target DBs and storage roots; restore roles/grants intentionally; restore both DBs and matching files; verify counts, constraints, sequence state, application/person links, document bytes/versions, signatures/history and asset ownership. Validate owner-only downloads and no public media exposure after restore.
6. Reconcile cross-service identities and outbox effects against the recovery marker. Revoke/rotate sessions or credentials only through the approved incident procedure. Do not restore a sent-message event as pending and resend automatically.
7. Rehearse on a clean host using representative synthetic volume, including loss of the primary host and recovery of encryption keys. Record actual restore time and data loss, operator actions and deviations. Schedule periodic drills and preserve signed-off evidence.

## Residual limits

No restore of a deployed backup, no production credentials or keys, no encrypted/off-site/PITR drill, no actual application-volume capacity result, no ACL/security-label restoration, no tested cross-service checkpoint, and no business RPO/RTO approval. This evidence supports a local recovery foundation only. Retention periods and legal deletion/hold policy require HR/privacy approval; historical recruitment/internship/legal records must never be automatically discarded during cleanup.
