"""Restore BOTH migrated synthetic application DBs; never uses .env or host DBs."""

import json
import subprocess
import time
from pathlib import Path

CONTAINER = "tvg-audit-pg-20261028"


def run(*args):
    return subprocess.check_output(
        ["docker", "exec", CONTAINER, *args], text=True
    ).strip()


def sql(database, query):
    return run(
        "psql",
        "-U",
        "tvg_audit",
        "-d",
        database,
        "-XAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    )


def fingerprints(database):
    tables = sql(
        database,
        "SELECT quote_ident(schemaname)||'.'||quote_ident(tablename) "
        "FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1",
    ).splitlines()
    rows = []
    for offset in range(0, len(tables), 25):
        selects = [
            f"SELECT '{table}', count(*), md5(coalesce(string_agg(row_to_json(t)::text, "
            f"chr(10) ORDER BY row_to_json(t)::text), '')) FROM {table} t"
            for table in tables[offset : offset + 25]
        ]
        rows.extend(sql(database, " UNION ALL ".join(selects)).splitlines())
    views = sql(
        database,
        "SELECT schemaname,viewname,md5(definition) FROM pg_views "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2",
    )
    constraints = sql(
        database,
        "SELECT n.nspname,c.relname,x.conname,pg_get_constraintdef(x.oid) "
        "FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3",
    )
    # pg_class includes identity-column sequences, unlike information_schema.sequences.
    sequence_names = sql(
        database,
        "SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind='S' AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1",
    ).splitlines()
    sequences = []
    for offset in range(0, len(sequence_names), 40):
        selects = [
            f"SELECT '{name}',last_value,is_called FROM {name}"
            for name in sequence_names[offset : offset + 40]
        ]
        sequences.extend(sql(database, " UNION ALL ".join(selects)).splitlines())
    return {
        "tables": sorted(rows),
        "views": views,
        "constraints": constraints,
        "sequences": sorted(sequences),
    }


label = subprocess.check_output(
    ["docker", "inspect", "--format", '{{index .Config.Labels "purpose"}}', CONTAINER],
    text=True,
).strip()
assert label == "synthetic-readiness-audit"
assert run("printenv", "POSTGRES_DB") == "tvg_audit"
results = []
for source in ("tvg_audit_django", "tvg_audit_careerhub"):
    target = source + "_restored_v2"
    started = time.perf_counter()
    before = fingerprints(source)
    run("pg_dump", "-U", "tvg_audit", "-Fc", "--file=/tmp/" + source + ".dump", source)
    run("createdb", "-U", "tvg_audit", target)
    run(
        "pg_restore",
        "-U",
        "tvg_audit",
        "--exit-on-error",
        "--no-owner",
        "-d",
        target,
        "/tmp/" + source + ".dump",
    )
    after = fingerprints(target)
    assert before == after, f"Synthetic restore mismatch: {source}"
    results.append(
        {
            "database": source,
            "restored_database": target,
            "table_count": len(before["tables"]),
            "sequence_count": len(before["sequences"]),
            "table_data_hashes_match": True,
            "views_match": True,
            "constraints_match": True,
            "sequences_match": True,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    )

result = {
    "date": "2026-10-28",
    "postgres": run("postgres", "--version"),
    "results": results,
    "scope": "Full migrated schemas with sparse synthetic records, not production volume, upgrades, PITR, encrypted/offsite recovery or cross-service recovery consistency.",
}
Path(__file__).with_name("evidence-application-restore.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps(result, indent=2))
