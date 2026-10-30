# Metabase reporting for PeopleOps analytics

This Phase 17 slice gives Metabase read-only access to published PeopleOps KPI
snapshots through one curated PostgreSQL view. It is disabled by default:
`TVG_ANALYTICS_EXPORT_PROVIDER=disabled` refuses every publication, no Metabase
service is part of the Compose files, and no database role exists until a DBA
creates it. Publishing makes no network call. The decision is recorded in
ADR-016; the KPI meanings are in `docs/analytics/PEOPLEOPS_KPIS.md`.

## Data ownership

- Horilla and the `tvg_*` apps: authoritative recruitment, internship,
  onboarding, asset, training, reporting and workflow records.
- `tvg_analytics`: KPI definitions, immutable snapshots, metric points,
  validation results, export attempts and small-cell flags, stored in the
  PeopleOps database tables `tvg_analytics_*`. Metabase never reads these
  tables directly.
- Schema `tvg_analytics_reporting`: the curated view `metric_point_v1`,
  created by migration `tvg_analytics.0002_snapshot_revisions_reporting_view`.
  The migration revokes PUBLIC access to the schema and the view and grants
  read access only to an existing `tvg_analytics_reader` role. It creates no
  login role and contains no secret.
- DBA: the `tvg_analytics_reader` group role, the `metabase_reader` login role
  and its password, created out of band with
  `docs/integrations/metabase_reader_role.sql`.
- Metabase: its users, groups, questions, dashboards and its own application
  database. It holds no HR records, only query results over aggregates.
- Cloudflare Access/Tunnel or a private network: transport to Metabase and to
  the PeopleOps analytics pages. It is not authorization.

## Reporting contract

Metabase reads `tvg_analytics_reporting.metric_point_v1` only. The view returns
one row per metric point of every active snapshot whose status is `exported`.
Snapshots that are building, validated but not published, failed or superseded
are not visible. After a newer revision of the same filters, period and as-of
date is published, the older one is superseded and disappears from the view.

Columns:

- snapshot: `snapshot_id` (public UUID), `schema_version`,
  `snapshot_revision`, `company_id`, `period_start`, `period_end`,
  `as_of_date`, `generated_at`, `exported_at`;
- snapshot filters: `filter_venture_id`, `filter_department_id`,
  `filter_role_id`, `filter_source`, `is_unfiltered`;
- metric: `dashboard`, `metric_key`;
- dimensions: `dim_source`, `dim_month`, `dim_screening_outcome`,
  `dim_venture`, `dim_venture_id`, `dim_department`, `dim_department_id`,
  `dim_role`, `dim_role_id`, `dim_stage`, `dim_status`, `dim_horizon_days`,
  `dim_checklist_item`;
- measures: `value`, `numerator`, `denominator`, `suppressed`.

When `suppressed` is true the point is a small cell and `value`, `numerator`
and `denominator` are empty. The column meanings, the small-cell rule and the
rules for avoiding double counting are in the "Metabase reporting contract"
section of `docs/analytics/PEOPLEOPS_KPIS.md`. In short: always select one
`company_id`, one filter set (`is_unfiltered` or one exact `filter_*`
combination), one `period_start`/`period_end`/`as_of_date` and one
`metric_key`; never sum across metrics, periods or cumulative horizons.

The view contains the published snapshots of every company in the database.
Metabase Open Source does not provide the configurable View Data or row/column
security controls available in paid editions; its View Data behavior defaults
to "Can view". The database role and curated view are therefore the primary
data boundary, not a Metabase UI setting. Until a per-company view or separate
connection exists, admit only Metabase users authorized to see the published
aggregates of every company that publishes, or publish from one company only.
See Metabase's current [data-permissions documentation](https://www.metabase.com/docs/latest/permissions/data)
and [database-role guidance](https://www.metabase.com/docs/latest/databases/users-roles-privileges).

`metric_point_v1` is a stable contract. A new column, a changed meaning or a new
dimension creates `metric_point_v2` alongside v1 in a new migration and needs a
`GRANT SELECT` to `tvg_analytics_reader`; v1 is never changed in place.

## Enablement sequence

1. Deploy the code. The web entrypoint runs `migrate` on start, so migration
   0002 creates the schema and view. It grants nothing until the reader role
   exists.
2. The DBA creates the roles and grants with `metabase_reader_role.sql` and
   keeps its verification output.
3. Run the role-denial check below.
4. Set `TVG_ANALYTICS_EXPORT_PROVIDER=reporting_view` and restart the
   PeopleOps web service.
5. Assign the analytics permissions through Horilla groups.
6. Build, review and publish a snapshot.
7. Configure Metabase and its access path.

## Database roles (DBA)

Requirements: a psql 15 or newer client (it uses `\getenv`), a private network
path to the PeopleOps PostgreSQL database, and a DBA role that can create roles
and grant on the `tvg_analytics_reporting` schema.

In the bundled Compose files the application connects as `POSTGRES_USER`, which
the official PostgreSQL image creates as a superuser. Metabase must never use
that role or any role the application uses; it uses only `metabase_reader`.
Use a dedicated administrative role for this procedure where one exists.

1. Check whether the server logs DDL text: `SHOW log_statement;`. The script
   turns statement logging off for its own session when run by a superuser.
   A non-superuser DBA must confirm that `log_statement` is not `ddl` or `all`,
   because the password is part of the `CREATE ROLE`/`ALTER ROLE` statement.
   If `pg_stat_statements` is installed and tracks utility statements, review
   it as well.
2. Put the password from the secret store into the environment without echoing
   it, run the script and remove the variable:

   ```sh
   read -rs METABASE_READER_PASSWORD && export METABASE_READER_PASSWORD
   psql "host=<db host> port=5432 dbname=<PeopleOps database> user=<DBA role>" \
        --set ON_ERROR_STOP=1 \
        --file docs/integrations/metabase_reader_role.sql
   unset METABASE_READER_PASSWORD
   ```

   A psql variable also works (`--set metabase_password=...`), but it is
   visible in the process list while psql runs. The script refuses a missing
   password or one shorter than 24 characters.
3. The script is idempotent. It creates `tvg_analytics_reader` (NOLOGIN) and
   `metabase_reader` (LOGIN, member of `tvg_analytics_reader`,
   `CONNECTION LIMIT 5`), sets `default_transaction_read_only = on`,
   `statement_timeout`, `idle_in_transaction_session_timeout` and
   `search_path = tvg_analytics_reporting` on `metabase_reader`, grants
   `CONNECT` on the database, `USAGE` on schema `tvg_analytics_reporting` and
   `SELECT` on `tvg_analytics_reporting.metric_point_v1` to the group role
   only, and grants nothing on schema `public` or on any table.
4. Grants and migration order:
   - if migration 0002 ran before the role existed, step 4 of the script
     applies the same conditional grants as the migration;
   - if the role existed before the migration ran, the migration grants by
     itself; run the script again afterwards to obtain the verification;
   - after any later migration that recreates the view or adds a view version,
     run the script again. Do not roll a production migration back and forward
     to obtain grants; in a disposable staging database, re-running the
     migration after the role exists has the same effect.
5. The verification at the end of the script prints the role attributes and
   privilege checks and fails the run unless: the reader can read the view;
   it cannot read `public.tvg_careers_application`,
   `public.employee_employee` or `public.tvg_analytics_analyticsmetricpoint`;
   it cannot create objects in either schema; it does not inherit
   `pg_read_all_data`; neither role has an elevated attribute; and the only
   relation privilege the roles hold is `SELECT` on the reporting view. If the
   `public` schema check fails, the cluster still grants `CREATE` on `public`
   to PUBLIC (a pre-PostgreSQL 15 default kept by an upgrade); revoke it after
   checking that no other role depends on it.
6. Role defaults such as `default_transaction_read_only` can be overridden by a
   client session. The grants, not the defaults, are the security control.

Optional hardening, decided by the DBA because it affects other roles: allow
`metabase_reader` in `pg_hba.conf` only from the Metabase host, and review the
`CONNECT` and `TEMPORARY` privileges that PostgreSQL grants to PUBLIC on every
database by default.

### Role-denial check

Connect as `metabase_reader` and confirm:

```sql
SELECT count(*) FROM tvg_analytics_reporting.metric_point_v1;     -- succeeds
SELECT 1 FROM public.tvg_careers_application LIMIT 1;              -- permission denied
SELECT 1 FROM public.employee_employee LIMIT 1;                    -- permission denied
SELECT 1 FROM public.tvg_analytics_analyticsmetricpoint LIMIT 1;   -- permission denied
CREATE TABLE tvg_analytics_reporting.denied_probe (id integer);    -- fails
```

### Rotation and revocation

- Rotate the password by running the script again with the new value, then
  update the Metabase connection. Alternatively, a DBA can run psql's
  interactive `\password metabase_reader`, which sends only a SCRAM hash.
- Revoke access immediately with `ALTER ROLE metabase_reader NOLOGIN;` and end
  open sessions with
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'metabase_reader';`.
- Setting `TVG_ANALYTICS_EXPORT_PROVIDER=disabled` stops new publications only.
  Snapshots already exported stay in the view until superseded; revoke the
  reader to stop Metabase reading them.

## Enabling publication

```text
TVG_ANALYTICS_EXPORT_PROVIDER=reporting_view
TVG_ANALYTICS_MIN_CELL_SIZE=5
```

Set these in `.env` for the production overlay (which passes them to the web,
PeopleHub and scheduler services) and restart the PeopleOps web service.
Allowed providers are `disabled` (default) and `reporting_view`. The
`reporting_view` publisher opens no network connection and writes nothing
outside the snapshot tables: it re-checks the batch for prohibited identifiers
and records the publication, and the view then shows the snapshot. The
`synthetic` exporter exists only in test settings; `get_exporter` and the
`tvg_analytics.E002` system check reject it when `HORILLA_ENV` or
`TVG_AUTOMATION_ENVIRONMENT` is `staging` or `production`. An unknown provider
fails `tvg_analytics.E001`, and a minimum cell size below 1 fails
`tvg_analytics.E003`. Raise `TVG_ANALYTICS_MIN_CELL_SIZE` only after a privacy
review; do not lower it below 5 without one. No Metabase password belongs in the
application environment.

## Permissions and publication

| Permission | Allows |
|---|---|
| `tvg_analytics.view_peopleops_analytics` | The PeopleOps analytics dashboards, snapshot pages and the two TVG Horilla standard reports. |
| `tvg_analytics.generate_analytics_snapshot` | Building and rebuilding snapshots. |
| `tvg_analytics.export_analytics_snapshot` | Publishing a validated snapshot to the reporting view. |

Assign them through Horilla groups and company-scoped group assignments; no
migration creates groups or assigns permissions. Suggested groups: "PeopleOps
analytics viewers" (view), "PeopleOps analytics builders" (view and generate)
and "PeopleOps analytics publishers" (view, generate and export). Every action
is limited to the employee's own company, and the dashboards refuse a different
selected Horilla company.

To publish: open `/peopleops/analytics/` on the private PeopleOps host, choose
the filters, select "Create validated snapshot" (or "Rebuild snapshot with
current data"), review the snapshot page, including the validation results and
the points marked "Withheld (small cell)", then select "Publish to the Metabase
reporting view". Each attempt is recorded. With the `disabled` provider the
attempt fails with `analytics_export_disabled` and the snapshot stays
`validated`.

## Metabase configuration

This repository adds no Metabase service. Deploy Metabase separately, on a
private network path to the PeopleOps database; do not publish the database
port on a public interface.

1. **Application database.** Give Metabase its own PostgreSQL database and role
   for its metadata (`MB_DB_TYPE=postgres`, `MB_DB_HOST`, `MB_DB_PORT`,
   `MB_DB_DBNAME`, `MB_DB_USER`, `MB_DB_PASS`). Never use the PeopleOps database
   or the embedded H2 file in production.
2. **Encryption and session keys.** Generate independent random values for
   `MB_ENCRYPTION_SECRET_KEY` and `MB_SESSION_SECRET_KEY`, store both outside
   the application database and use the same values on every Metabase node.
   The encryption key protects supported stored secrets including the reader
   password; losing it can make them unrecoverable. The session key prevents
   application-database access alone from creating usable sessions. Follow the
   current [self-hosting security guidance](https://www.metabase.com/docs/latest/installation-and-operation/security-best-practices-self-hosting)
   before adding the PeopleOps connection.
3. **Connection pool.** Keep Metabase's data-warehouse connection pool within
   the role's limit of five, for example
   `MB_JDBC_DATA_WAREHOUSE_MAX_CONNECTION_POOL_SIZE=5`.
4. **Database connection.** Add a PostgreSQL database with user
   `metabase_reader` and the password from the secret store. Under schema
   selection choose "Only these" and enter `tvg_analytics_reporting`, so only
   `metric_point_v1` is synced. Use TLS when the connection leaves the host.
5. **Sharing.** Keep public sharing (`MB_ENABLE_PUBLIC_SHARING=false`) and
   embedding disabled. A public link would bypass Cloudflare Access.
6. **Groups and collections.** In Open Source, do not rely on the paid View
   Data controls. Put the approved questions and dashboards in a restricted
   collection, remove Create Queries access for ordinary users, and limit
   native SQL to Metabase administrators. Every user with any access to this
   connection must be authorized for every company's aggregates it contains.
   Model actions stay disabled; the database role is read-only in any case.
   Remember that Metabase combines group memberships permissively and every
   user belongs to All Users; review the current
   [permissions guidance](https://www.metabase.com/docs/latest/permissions/introduction).

### Dashboard filters

| Metabase filter | Column | Use |
|---|---|---|
| Company | `company_id` | Required; one value. |
| All ventures, departments, roles and sources | `is_unfiltered` | Set to true for company-wide snapshots. |
| Venture (snapshot scope) | `filter_venture_id` | Selects snapshots built with that venture filter; all their metrics are narrowed. |
| Department (snapshot scope) | `filter_department_id` | As above, for the department filter. |
| Role (snapshot scope) | `filter_role_id` | As above, for the role (job position) filter. |
| Source (snapshot scope) | `filter_source` | As above, for the CareerHub source filter. |
| Period | `period_start`, `period_end` | One exact pair; do not use a date range across snapshots. |
| As-of date | `as_of_date` | One value; equals the period end for snapshots built in the UI. |
| Metric | `metric_key`, `dashboard` | One metric per card. |
| Breakdown | `dim_source`, `dim_month`, `dim_screening_outcome`, `dim_venture`, `dim_venture_id`, `dim_department`, `dim_department_id`, `dim_role`, `dim_role_id`, `dim_stage`, `dim_status`, `dim_checklist_item` | Splits one breakdown metric inside one snapshot. |
| Horizon | `dim_horizon_days` | One value for `upcoming_starts` and `upcoming_completions`. |
| Withheld values | `suppressed` | Show as a note; never fill withheld values. |

A `filter_*` value selects a different published snapshot whose every metric
was computed for that scope. A `dim_*` value selects one row of a breakdown in
the current snapshot. Use `filter_*` for a dashboard-wide venture, department,
role or source filter, and `dim_*` only inside a breakdown card. The reader
cannot read venture, department or job-position tables; to show names for the
numeric `filter_*` ids, configure a custom value mapping in Metabase from the
`dim_venture`/`dim_venture_id` (and department and role) pairs.

## Access path

- Metabase and the PeopleOps analytics pages are staff-only. Reach them through
  a Cloudflare Access application restricted to the approved staff group (with
  Cloudflare Tunnel) or through a private network.
- Never add Metabase, or a route to `/peopleops/analytics/`, to the public
  server blocks in `docker/nginx.conf` (`people.thevipgroup.eu` routes to
  PeopleHub, `career.thevipgroup.eu` to CareerHub). The analytics pages are
  mounted only on the PeopleOps web URLconf, which has no host port in the
  production overlay.
- Cloudflare Access is transport. Metabase's own login and group permissions
  and Horilla's permissions still apply.

## Staging acceptance

Before enabling `reporting_view` in production:

1. Use an isolated PostgreSQL 16 database with synthetic data only. The focused
   test fixtures exercise the full lifecycle without external calls. For a
   browser review, `seed_tvg_career_demo --confirm-synthetic` can create
   synthetic openings; create any later synthetic lifecycle records only in
   the isolated environment through their normal workflows, never in a copy of
   production.
2. Apply migrations and confirm that
   `tvg_analytics_reporting.metric_point_v1` exists and no public-schema
   analytics view was created.
3. Run `metabase_reader_role.sql`; the verification block must pass. Keep the
   privilege output (no password) as evidence for SECURITY_RISKS pre-production
   item 2.
4. Run the role-denial check as `metabase_reader`: the view is readable;
   `tvg_careers_application`, `employee_employee` and
   `tvg_analytics_analyticsmetricpoint` are denied; creating a table fails.
5. With the `disabled` provider, publishing records a failed
   `analytics_export_disabled` attempt and the view returns no rows.
6. Set `reporting_view`, restart, and confirm `python manage.py check` reports
   no `tvg_analytics.E001`, `E002` or `E003`.
7. Build a snapshot: every validation result passes and the view still returns
   no rows. Publish it: the view returns rows for that snapshot only, and
   suppressed rows have empty measures.
8. Rebuild and publish again: only the new revision is visible in the view.
9. A user without the export permission cannot publish (HTTP 403); a user of
   another company cannot open the snapshot.
10. In Metabase: the connection uses `metabase_reader`, only
    `tvg_analytics_reporting` is synced, public sharing is off, anonymous
    requests are stopped by Cloudflare Access, and a Metabase user without the
    restricted collection and without Create Queries access cannot view or
    construct analytics questions.
11. For one published snapshot, Metabase values equal the snapshot page for
    every unsuppressed point.
12. No e-mail is sent and no external service is called during the run.
