"""Negative configuration checks without loading .env, Django apps or any DB."""

import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured

ROOT = Path(__file__).resolve().parents[3]
security = runpy.run_path(str(ROOT / "horilla/settings/security.py"))
observations = []
for name, secret, hosts in (
    ("reject_short_private_secret", "x", ["people.example.invalid"]),
    (
        "reject_mixed_wildcard_hosts",
        "synthetic-audit-not-for-deployment-" * 3,
        ["people.example.invalid", "*"],
    ),
):
    rejected = False
    try:
        security["validate_production_secrets"](secret, hosts, "synthetic-init-only")
    except ImproperlyConfigured:
        rejected = True
    observations.append({"check": name, "passed": rejected})

with patch.dict(os.environ, {"DEBUG": "1", "HORILLA_ENV": "production"}, clear=True):
    rejected = False
    try:
        public = runpy.run_path(str(ROOT / "horilla/settings/careerhub.py"))
    except ImproperlyConfigured:
        rejected = True
    observations.append(
        {
            "check": "reject_production_careerhub_debug",
            "passed": rejected,
            "secure_session_cookie": (
                None if rejected else public["SESSION_COOKIE_SECURE"]
            ),
        }
    )

result = {
    "date": "2026-10-28",
    "scope": "isolated settings execution, not deployed settings",
    "checks": observations,
    "passed": all(o["passed"] for o in observations),
}
Path(__file__).with_name("evidence-configuration.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
