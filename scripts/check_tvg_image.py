"""Inspect a locally built release image without starting the application.

The check never contacts a database, cache, mail server or external network. It
uses ``docker image inspect`` plus a short Python process in a read-only,
network-disabled container. It does not run the image entrypoint, migrations,
collectstatic, Gunicorn or any background job.
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "image",
    nargs="?",
    default="tvg-peopleops-readiness:2026-10-29",
    help="Local image tag or immutable digest to inspect",
)
parser.add_argument(
    "--expected-purpose",
    help="Require this exact value in the optional image purpose label",
)
parser.add_argument("--report", type=Path)
args = parser.parse_args()


def run(command):
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=60, check=False
    )
    if completed.returncode:
        raise SystemExit(
            f"Image contract command failed ({completed.returncode}); "
            "the application was not started."
        )
    return completed.stdout


metadata = json.loads(run(["docker", "image", "inspect", args.image]))[0]
probe_source = r"""
import importlib.metadata
import importlib.util
import json
import os
import pathlib
import sys

root = pathlib.Path('/app')
media = root / 'media'
payload = {
    'python': sys.version.split()[0],
    'django': importlib.metadata.version('Django'),
    'djangorestframework': importlib.metadata.version('djangorestframework'),
    'msgpack': importlib.metadata.version('msgpack'),
    'urllib3': importlib.metadata.version('urllib3'),
    'uid': os.getuid(),
    'required_files': all((root / item).is_file() for item in (
        'horilla/settings/tvg.py',
        'horilla/settings/careerhub.py',
        'horilla/settings/peoplehub.py',
        'tvg_careers/accounts/migrations/0006_arrangement_security_events.py',
        'docker/gunicorn.conf.py',
    )),
    'local_settings_absent': not (root / 'horilla/settings/local_settings.py').exists(),
    'environment_files_absent': not any(
        item.name != '.env.dist' for item in root.glob('.env*')
    ),
    'bundled_private_data_absent': not any(media.rglob('*')),
    'runtime_package_managers_absent': (
        importlib.util.find_spec('pip') is None
        and importlib.util.find_spec('setuptools') is None
        and not any(pathlib.Path('/opt/venv/bin').glob('pip*'))
    ),
}
print(json.dumps(payload))
"""
probe = json.loads(
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--entrypoint",
            "python",
            args.image,
            "-c",
            probe_source,
        ]
    )
)


def version_at_least(actual, expected):
    def numeric(value):
        return tuple(int(part) for part in value.split(".")[:3])

    return numeric(actual) >= expected


checks = [
    {
        "check": "immutable-image-id-resolved",
        "passed": metadata.get("Id", "").startswith("sha256:"),
    },
    {
        "check": "configured-non-root-user",
        "passed": metadata.get("Config", {}).get("User") == "appuser",
    },
    {"check": "runtime-python-3.12", "passed": probe["python"].startswith("3.12.")},
    {
        "check": "patched-django-minimum",
        "passed": version_at_least(probe["django"], (5, 2, 18)),
    },
    {
        "check": "reviewed-drf-version",
        "passed": version_at_least(probe["djangorestframework"], (3, 18, 3)),
    },
    {
        "check": "patched-msgpack-minimum",
        "passed": version_at_least(probe["msgpack"], (1, 2, 3)),
    },
    {
        "check": "patched-urllib3-minimum",
        "passed": version_at_least(probe["urllib3"], (2, 8, 0)),
    },
    {"check": "runtime-uid-is-non-root", "passed": probe["uid"] != 0},
    {"check": "required-release-files-shipped", "passed": probe["required_files"]},
    {"check": "local-settings-excluded", "passed": probe["local_settings_absent"]},
    {"check": "environment-files-excluded", "passed": probe["environment_files_absent"]},
    {
        "check": "private-media-not-bundled",
        "passed": probe["bundled_private_data_absent"],
    },
    {
        "check": "runtime-package-managers-absent",
        "passed": probe["runtime_package_managers_absent"],
    },
]
if args.expected_purpose:
    checks.insert(
        1,
        {
            "check": "expected-purpose-label",
            "passed": metadata.get("Config", {}).get("Labels", {}).get("purpose")
            == args.expected_purpose,
        },
    )
result = {
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "scope": (
        "local image metadata and a read-only, network-disabled Python probe; "
        "entrypoint and application services were not started"
    ),
    "requested_image": args.image,
    "image_id": metadata.get("Id"),
    "probe": probe,
    "checks": checks,
    "passed": all(item["passed"] for item in checks),
}
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
