# Curated reporting-view migration for the Phase 17 analytics schema.
# the curated Metabase reporting view (see ADR-016 and
# docs/integrations/METABASE_REPORTING.md).
#
# Security: this migration creates no login role and contains no secret. On
# PostgreSQL it creates the dedicated ``tvg_analytics_reporting`` schema,
# revokes PUBLIC access to it and grants read access only to an existing
# ``tvg_analytics_reader`` group role that a DBA created out of band.

from django.db import migrations

LEGACY_VIEW = "tvg_analytics_metabase_metric_v1"
LEGACY_VIEW_SQL = """
CREATE VIEW tvg_analytics_metabase_metric_v1 AS
SELECT
    p.id AS metric_point_id,
    s.public_id AS snapshot_public_id,
    s.company_id,
    s.as_of_date,
    s.status AS snapshot_status,
    p.dashboard,
    p.metric_key,
    p.period_start,
    p.period_end,
    p.value,
    p.numerator,
    p.denominator,
    p.dimensions
FROM tvg_analytics_analyticsmetricpoint p
JOIN tvg_analytics_analyticssnapshot s ON s.id = p.snapshot_id
WHERE s.status IN ('validated', 'exported')
"""

REPORTING_SCHEMA = "tvg_analytics_reporting"
READER_ROLE = "tvg_analytics_reader"
POSTGRES_VIEW = f"{REPORTING_SCHEMA}.metric_point_v1"
SQLITE_VIEW = f"{REPORTING_SCHEMA}_metric_point_v1"
FILTER_COLUMNS = ("venture_id", "department_id", "role_id", "source")
INTEGER_KEYS = frozenset({"venture_id", "department_id", "role_id"})
# Frozen copy of the dimension vocabulary for this view version. A new
# dimension requires a new view version (metric_point_v2), never an edit here.
DIMENSION_COLUMNS = (
    "source",
    "month",
    "screening_outcome",
    "venture",
    "venture_id",
    "department",
    "department_id",
    "role",
    "role_id",
    "stage",
    "status",
    "horizon_days",
    "checklist_item",
)


def _select(vendor):
    if vendor == "postgresql":

        def extract(column, key):
            value = f"NULLIF({column} ->> '{key}', '')"
            return f"{value}::bigint" if key in INTEGER_KEYS else value

        suppressed = "p.is_small_cell"
        active = "s.is_active AND p.is_active"
    else:

        def extract(column, key):
            value = f"json_extract({column}, '$.{key}')"
            return f"CAST({value} AS INTEGER)" if key in INTEGER_KEYS else value

        suppressed = "p.is_small_cell = 1"
        active = "s.is_active = 1 AND p.is_active = 1"

    filters = [f"{extract('s.filters', key)} AS filter_{key}" for key in FILTER_COLUMNS]
    unfiltered = " AND ".join(
        f"{extract('s.filters', key)} IS NULL" for key in FILTER_COLUMNS
    )
    dimensions = [
        f"{extract('p.dimensions', key)} AS dim_{key}" for key in DIMENSION_COLUMNS
    ]
    measures = [
        f"CASE WHEN {suppressed} THEN NULL ELSE p.{name} END AS {name}"
        for name in ("value", "numerator", "denominator")
    ]
    columns = [
        "s.public_id AS snapshot_id",
        "s.schema_version",
        "s.revision AS snapshot_revision",
        "s.company_id",
        "s.period_start",
        "s.period_end",
        "s.as_of_date",
        "s.generated_at",
        "s.exported_at",
        *filters,
        f"({unfiltered}) AS is_unfiltered",
        "p.dashboard",
        "p.metric_key",
        *dimensions,
        *measures,
        f"({suppressed}) AS suppressed",
    ]
    return (
        "SELECT\n    "
        + ",\n    ".join(columns)
        + "\nFROM tvg_analytics_analyticsmetricpoint p\n"
        "JOIN tvg_analytics_analyticssnapshot s ON s.id = p.snapshot_id\n"
        f"WHERE s.status = 'exported' AND {active}"
    )


def reporting_view_statements(vendor):
    """DDL for the curated reporting view; also executed by the tests."""

    if vendor == "postgresql":
        return [
            f"CREATE SCHEMA IF NOT EXISTS {REPORTING_SCHEMA}",
            f"REVOKE ALL ON SCHEMA {REPORTING_SCHEMA} FROM PUBLIC",
            f"DROP VIEW IF EXISTS {POSTGRES_VIEW}",
            f"CREATE VIEW {POSTGRES_VIEW} WITH (security_barrier = true) AS\n"
            + _select(vendor),
            f"REVOKE ALL ON {POSTGRES_VIEW} FROM PUBLIC",
            f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READER_ROLE}') THEN
        GRANT USAGE ON SCHEMA {REPORTING_SCHEMA} TO {READER_ROLE};
        GRANT SELECT ON {POSTGRES_VIEW} TO {READER_ROLE};
    END IF;
END
$$
""",
        ]
    if vendor == "sqlite":
        return [
            f"DROP VIEW IF EXISTS {SQLITE_VIEW}",
            f"CREATE VIEW {SQLITE_VIEW} AS\n" + _select(vendor),
        ]
    return []


def drop_view_statements(vendor):
    if vendor == "postgresql":
        return [f"DROP VIEW IF EXISTS {POSTGRES_VIEW}"]
    if vendor == "sqlite":
        return [f"DROP VIEW IF EXISTS {SQLITE_VIEW}"]
    return []


def create_reporting_view(apps, schema_editor):
    for statement in reporting_view_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement)


def drop_reporting_view(apps, schema_editor):
    for statement in drop_view_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("tvg_analytics", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_reporting_view, drop_reporting_view),
    ]
