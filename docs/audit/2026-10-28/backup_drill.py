"""Disposable PostgreSQL and synthetic private-file restore drill."""

import hashlib
import json
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

CONTAINER = "tvg-audit-pg-20261028"


def run(*args):
    return subprocess.check_output(
        ["docker", "exec", CONTAINER, *args], text=True
    ).strip()


assert run("printenv", "POSTGRES_DB") == "tvg_audit"
started = time.perf_counter()
run(
    "psql",
    "-U",
    "tvg_audit",
    "-d",
    "tvg_audit",
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    "CREATE TABLE audit_person (id integer PRIMARY KEY, label text NOT NULL); "
    "CREATE TABLE audit_application (id integer PRIMARY KEY, person_id integer REFERENCES audit_person(id), venture text); "
    "INSERT INTO audit_person VALUES (1, 'Completely fictional audit candidate'); "
    "INSERT INTO audit_application VALUES (1,1,'FluxOps'),(2,1,'FindMyStay');",
)
run("pg_dump", "-U", "tvg_audit", "-Fc", "--file=/tmp/tvg-audit.dump", "tvg_audit")
run("createdb", "-U", "tvg_audit", "tvg_audit_restore")
run(
    "pg_restore",
    "-U",
    "tvg_audit",
    "--exit-on-error",
    "--no-owner",
    "-d",
    "tvg_audit_restore",
    "/tmp/tvg-audit.dump",
)
query = "SELECT p.id,p.label,a.id,a.venture FROM audit_person p JOIN audit_application a ON a.person_id=p.id ORDER BY a.id"
original = run("psql", "-U", "tvg_audit", "-d", "tvg_audit", "-At", "-c", query)
restored = run("psql", "-U", "tvg_audit", "-d", "tvg_audit_restore", "-At", "-c", query)
assert restored == original
roots = [
    "media",
    "careerhub-private-media",
    "verification-private-media",
    "acceptance-private-media",
    "erasmus-document-private-media",
    "onboarding-private-media",
    "reporting-private-media",
    "offboarding-private-media",
]
with tempfile.TemporaryDirectory(prefix="tvg-audit-restore-") as tmp:
    base = Path(tmp)
    expected = {}
    for root in roots:
        path = base / "source" / root / "synthetic.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"%PDF-1.4\nSYNTHETIC AUDIT DOCUMENT ONLY\n%%EOF")
        expected[str(path.relative_to(base / "source"))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    archive = base / "synthetic.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name in expected:
            z.write(base / "source" / name, name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        z.extractall(base / "restored")  # archive created here, no untrusted members
    actual = {
        str(p.relative_to(base / "restored")): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in (base / "restored").rglob("*.pdf")
    }
    assert actual == expected
result = {
    "date": "2026-10-28",
    "postgres": run("postgres", "--version"),
    "database_restore_equal": True,
    "synthetic_persons": 1,
    "synthetic_applications": 2,
    "synthetic_file_roots_verified": roots,
    "elapsed_seconds": round(time.perf_counter() - started, 2),
    "scope": "toolchain smoke drill only; not a full PeopleOps/CareerHub backup, encryption, PITR, RPO or RTO certification",
}
Path(__file__).with_name("evidence-backup.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps(result, indent=2))
