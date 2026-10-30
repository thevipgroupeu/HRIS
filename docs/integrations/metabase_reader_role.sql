-- THEVIPGROUP PeopleOps analytics: Metabase reader roles (ADR-016).
--
-- Run manually by a DBA, once per environment, with psql 15 or newer, connected
-- to the PeopleOps (Horilla) database as a role that may create roles and grant
-- on the tvg_analytics_reporting schema. Never run this file from a Django
-- migration, the container entrypoint, CI or Compose.
--
-- The file is idempotent. Re-running it re-applies role attributes, settings
-- and grants, sets the password to the supplied value and repeats the
-- verification. It contains no secret. Supply the password through the
-- environment (preferred: it stays out of shell history and the process list):
--
--   export METABASE_READER_PASSWORD   # value read from the secret store
--   psql "host=<db host> dbname=<PeopleOps database> user=<DBA role>" \
--        --set ON_ERROR_STOP=1 \
--        --file docs/integrations/metabase_reader_role.sql
--
-- or as a psql variable with --set metabase_password=<value>, which is visible
-- in the process list while psql runs.
--
-- Result:
--   tvg_analytics_reader  NOLOGIN group role. Holds CONNECT on this database,
--                         USAGE on schema tvg_analytics_reporting and SELECT on
--                         tvg_analytics_reporting.metric_point_v1 only.
--   metabase_reader       LOGIN role used by Metabase. Member of
--                         tvg_analytics_reader, read-only by default, five
--                         connections at most, search_path limited to the
--                         reporting schema. It receives no direct grant.
--
-- Nothing is granted on the public schema or on any base table. The view runs
-- with its owner's privileges, so the reader needs no access to the tables it
-- reads. See docs/integrations/METABASE_REPORTING.md.

\set ON_ERROR_STOP on
\set ECHO none

\getenv metabase_password METABASE_READER_PASSWORD
\if :{?metabase_password}
\else
DO $$
BEGIN
    RAISE EXCEPTION 'Set METABASE_READER_PASSWORD (or --set metabase_password=...). Nothing was changed.';
END
$$;
\endif

-- Keep the password out of the server log where this session is allowed to.
-- Only a superuser can change these settings; other DBAs must confirm that
-- log_statement is not 'ddl' or 'all' before running this file.
SELECT current_setting('is_superuser') = 'on' AS tvg_dba_is_superuser \gset
\if :tvg_dba_is_superuser
SET log_statement = 'none';
SET log_min_duration_statement = -1;
SET log_min_error_statement = 'panic';
\endif

SELECT length(:'metabase_password') >= 24 AS metabase_password_ok \gset
\if :metabase_password_ok
\else
DO $$
BEGIN
    RAISE EXCEPTION 'The Metabase reader password must be at least 24 characters. Nothing was changed.';
END
$$;
\endif

BEGIN;

-- 1. NOLOGIN group role that carries every reporting privilege.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tvg_analytics_reader') THEN
        CREATE ROLE tvg_analytics_reader
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE tvg_analytics_reader NOLOGIN;

-- 2. LOGIN role for Metabase. The statement is generated so the password is
--    passed as a quoted literal from the psql variable; it is not stored here.
SELECT format(
    'CREATE ROLE metabase_reader LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB '
    'NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT CONNECTION LIMIT 5 '
    'IN ROLE tvg_analytics_reader',
    :'metabase_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_reader')
UNION ALL
SELECT format(
    'ALTER ROLE metabase_reader WITH LOGIN INHERIT CONNECTION LIMIT 5 PASSWORD %L',
    :'metabase_password'
)
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_reader')
\gexec
\unset metabase_password

GRANT tvg_analytics_reader TO metabase_reader;

-- 3. Session defaults for the Metabase connection. A client can override a
--    role default, so these are defence in depth; the grants are the control.
ALTER ROLE metabase_reader SET default_transaction_read_only = on;
ALTER ROLE metabase_reader SET statement_timeout = '60s';
ALTER ROLE metabase_reader SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE metabase_reader SET search_path = tvg_analytics_reporting;

-- 4. Database and reporting-schema grants, to the group role only.
GRANT CONNECT ON DATABASE :"DBNAME" TO tvg_analytics_reader;

-- Same conditional grants as migration
-- tvg_analytics.0002_snapshot_revisions_reporting_view. When the schema or view
-- does not exist yet, apply the migrations and run this file again; when the
-- role exists before the migration runs, the migration grants by itself.
DO $$
BEGIN
    IF to_regnamespace('tvg_analytics_reporting') IS NULL THEN
        RAISE NOTICE 'Schema tvg_analytics_reporting does not exist yet: apply the tvg_analytics migrations, then run this file again.';
        RETURN;
    END IF;
    GRANT USAGE ON SCHEMA tvg_analytics_reporting TO tvg_analytics_reader;
    IF to_regclass('tvg_analytics_reporting.metric_point_v1') IS NULL THEN
        RAISE NOTICE 'View tvg_analytics_reporting.metric_point_v1 does not exist yet: apply the tvg_analytics migrations, then run this file again.';
        RETURN;
    END IF;
    GRANT SELECT ON tvg_analytics_reporting.metric_point_v1 TO tvg_analytics_reader;
END
$$;

COMMIT;

-- 5. Verification. Expected values are in the column comments; the final block
--    fails the run when any expectation is not met.
DO $$
BEGIN
    IF to_regclass('tvg_analytics_reporting.metric_point_v1') IS NULL THEN
        RAISE EXCEPTION 'Roles are configured, but the reporting view is missing: apply the tvg_analytics migrations, then run this file again to verify.';
    END IF;
END
$$;

SELECT
    rolname,
    rolcanlogin,     -- metabase_reader: true; tvg_analytics_reader: false
    rolsuper,        -- false
    rolcreaterole,   -- false
    rolcreatedb,     -- false
    rolreplication,  -- false
    rolbypassrls,    -- false
    rolconnlimit     -- metabase_reader: 5
FROM pg_roles
WHERE rolname IN ('tvg_analytics_reader', 'metabase_reader')
ORDER BY rolname;

SELECT
    has_table_privilege('metabase_reader', 'tvg_analytics_reporting.metric_point_v1', 'SELECT')
        AS reads_reporting_view,                 -- true
    has_schema_privilege('metabase_reader', 'tvg_analytics_reporting', 'CREATE')
        AS creates_in_reporting_schema,          -- false
    has_schema_privilege('metabase_reader', 'public', 'CREATE')
        AS creates_in_public_schema,             -- false
    has_table_privilege('metabase_reader', 'public.tvg_careers_application', 'SELECT')
        AS reads_applications,                   -- false
    has_table_privilege('metabase_reader', 'public.employee_employee', 'SELECT')
        AS reads_employees,                      -- false
    has_table_privilege('metabase_reader', 'public.tvg_analytics_analyticsmetricpoint', 'SELECT')
        AS reads_metric_point_table,             -- false
    pg_has_role('metabase_reader', 'pg_read_all_data', 'USAGE')
        AS has_read_all_data;                    -- false

-- Every relation privilege held by the two roles. Expected: exactly one row,
-- tvg_analytics_reporting | metric_point_v1 | SELECT | tvg_analytics_reader.
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       acl.privilege_type, r.rolname AS grantee
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(c.relacl) AS acl
JOIN pg_roles r ON r.oid = acl.grantee
WHERE r.rolname IN ('tvg_analytics_reader', 'metabase_reader')
ORDER BY 1, 2, 3, 4;

DO $$
DECLARE
    problems text[] := ARRAY[]::text[];
BEGIN
    IF NOT has_table_privilege('metabase_reader', 'tvg_analytics_reporting.metric_point_v1', 'SELECT') THEN
        problems := problems || 'metabase_reader cannot read the reporting view'::text;
    END IF;
    IF has_schema_privilege('metabase_reader', 'tvg_analytics_reporting', 'CREATE') THEN
        problems := problems || 'metabase_reader can create objects in the reporting schema'::text;
    END IF;
    IF has_schema_privilege('metabase_reader', 'public', 'CREATE') THEN
        problems := problems || 'metabase_reader can create objects in schema public (revoke CREATE on it from PUBLIC)'::text;
    END IF;
    IF has_table_privilege('metabase_reader', 'public.tvg_careers_application', 'SELECT') THEN
        problems := problems || 'metabase_reader can read tvg_careers_application'::text;
    END IF;
    IF has_table_privilege('metabase_reader', 'public.employee_employee', 'SELECT') THEN
        problems := problems || 'metabase_reader can read employee_employee'::text;
    END IF;
    IF has_table_privilege('metabase_reader', 'public.tvg_analytics_analyticsmetricpoint', 'SELECT') THEN
        problems := problems || 'metabase_reader can read the unsuppressed metric point table'::text;
    END IF;
    IF pg_has_role('metabase_reader', 'pg_read_all_data', 'USAGE') THEN
        problems := problems || 'metabase_reader inherits pg_read_all_data'::text;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname IN ('tvg_analytics_reader', 'metabase_reader')
          AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
    ) THEN
        problems := problems || 'a reader role has an elevated attribute'::text;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tvg_analytics_reader' AND rolcanlogin) THEN
        problems := problems || 'tvg_analytics_reader can log in'::text;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) AS acl
        JOIN pg_roles r ON r.oid = acl.grantee
        WHERE r.rolname IN ('tvg_analytics_reader', 'metabase_reader')
          AND NOT (
              n.nspname = 'tvg_analytics_reporting'
              AND c.relname = 'metric_point_v1'
              AND acl.privilege_type = 'SELECT'
              AND r.rolname = 'tvg_analytics_reader'
          )
    ) THEN
        problems := problems || 'a reader role holds a relation privilege other than SELECT on the reporting view'::text;
    END IF;
    IF cardinality(problems) > 0 THEN
        RAISE EXCEPTION 'Metabase reader verification failed: %', array_to_string(problems, '; ');
    END IF;
    RAISE NOTICE 'Metabase reader verification passed.';
END
$$;
