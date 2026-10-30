"""Read-only Compose contract check using synthetic configuration, never .env.

Only `docker compose config --no-env-resolution` is invoked. No service is
created, started, stopped or migrated. The rendered config is kept in memory.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path)
args = parser.parse_args()
files = [
    ROOT / name
    for name in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.tunnel.yml",
    )
]
source = "\n".join(path.read_text(encoding="utf-8") for path in files)
required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", source))
synthetic = {
    name: f"synthetic-contract-only-{name}-0000000000000000000000000000"
    for name in required
}
synthetic.update(
    ALLOWED_HOSTS="web,people.thevipgroup.eu",
    CSRF_TRUSTED_ORIGINS="https://people.thevipgroup.eu",
    DATABASE_URL="postgres://synthetic:synthetic@db:5432/synthetic",
    REDIS_URL="redis://:synthetic@redis:6379/0",
    TVG_LEGAL_EMPLOYER_ID="1",
    CAREERHUB_EMAIL_HOST="synthetic-mail.example.invalid",
    CAREERHUB_PRIVACY_NOTICE_VERSION="synthetic-only",
)
env = {
    key: value
    for key, value in os.environ.items()
    if key.upper()
    in {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "COMSPEC",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOME",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
    }
}
with tempfile.TemporaryDirectory(prefix="tvg-compose-contract-") as tmp:
    fake_env = Path(tmp) / "synthetic.env"
    fake_env.write_text(
        "\n".join(f"{key}={value}" for key, value in synthetic.items()),
        encoding="utf-8",
    )
    command = [
        "docker",
        "compose",
        "--env-file",
        str(fake_env),
        "--project-name",
        "tvg-synthetic-contract",
    ]
    for path in files:
        command += ["-f", str(path)]
    command += ["config", "--no-env-resolution", "--format", "json"]
    rendered = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
    )
    if rendered.returncode:
        raise SystemExit(
            "Compose contract could not be rendered; no live configuration was read."
        )
    services = json.loads(rendered.stdout)["services"]
checks = []


def record(name, passed):
    checks.append({"check": name, "passed": bool(passed)})


record(
    "only-loopback-tunnel-port-published",
    services["nginx"].get("ports")
    == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8080,
            "published": "8080",
            "protocol": "tcp",
        }
    ],
)
record(
    "application-database-cache-ports-not-published",
    all(
        not services[name].get("ports")
        for name in (
            "web",
            "careerhub",
            "peoplehub",
            "db",
            "careerhub_db",
            "redis",
            "careerhub_redis",
        )
    ),
)
record(
    "production-does-not-bind-mount-source",
    all(
        all(volume["type"] != "bind" for volume in services[name].get("volumes", []))
        for name in ("web", "careerhub", "peoplehub", "scheduler")
    ),
)
record(
    "startup-does-not-migrate",
    services["web"]["environment"].get("HORILLA_SKIP_RELEASE_TASKS") == "1"
    and services["scheduler"]["environment"].get("HORILLA_SKIP_RELEASE_TASKS") == "1"
    and services["careerhub"].get("entrypoint") == []
    and services["peoplehub"].get("entrypoint") == [],
)
record(
    "separate-database-volumes",
    services["db"]["volumes"][0]["source"]
    != services["careerhub_db"]["volumes"][0]["source"],
)
record("single-scheduler", services["scheduler"]["deploy"]["replicas"] == 1)
record(
    "strict-public-and-staff-hosts",
    services["careerhub"]["environment"]["CAREERHUB_ALLOWED_HOSTS"]
    == "career.thevipgroup.eu"
    and services["peoplehub"]["environment"]["PEOPLEHUB_ALLOWED_HOSTS"]
    == "people.thevipgroup.eu",
)
record(
    "healthchecks-validate-host-scheme-and-response",
    all(
        "X-Forwarded-Proto" in " ".join(services[name]["healthcheck"]["test"])
        and "r.status==200" in " ".join(services[name]["healthcheck"]["test"])
        for name in ("web", "careerhub", "peoplehub")
    ),
)
result = {
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "scope": "read-only Compose rendering with synthetic env; service env files not loaded",
    "checks": checks,
    "passed": all(item["passed"] for item in checks),
}
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
