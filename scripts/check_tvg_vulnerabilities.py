"""Create concise vulnerability evidence for a local release image.

The application is never started. Trivy reads the local Docker image and emits
only HIGH/CRITICAL vulnerabilities with available fixes. Its database cache
contains public scanner metadata only and can be reused between local runs.
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("image")
parser.add_argument("--report", type=Path)
parser.add_argument("--skip-db-update", action="store_true")
parser.add_argument("--cache-volume", default="tvg-trivy-cache-20261029")
args = parser.parse_args()

command = [
    "docker",
    "run",
    "--rm",
    "-v",
    "/var/run/docker.sock:/var/run/docker.sock",
    "-v",
    f"{args.cache_volume}:/root/.cache/trivy",
    "aquasec/trivy:0.58.1",
    "image",
    "--scanners",
    "vuln",
    "--severity",
    "CRITICAL,HIGH",
    "--ignore-unfixed",
    "--no-progress",
    "--format",
    "json",
]
if args.skip_db_update:
    command += ["--skip-db-update", "--skip-java-db-update"]
command.append(args.image)
completed = subprocess.run(
    command, capture_output=True, text=True, timeout=600, check=False
)
if completed.returncode:
    raise SystemExit(
        "Trivy could not scan the local image; no passing evidence was written."
    )
raw = json.loads(completed.stdout)
findings = []
for target in raw.get("Results", []):
    for item in target.get("Vulnerabilities") or []:
        findings.append(
            {
                "target": target.get("Target"),
                "id": item.get("VulnerabilityID"),
                "package": item.get("PkgName"),
                "installed": item.get("InstalledVersion"),
                "fixed": item.get("FixedVersion"),
                "severity": item.get("Severity"),
            }
        )
metadata = raw.get("Metadata") or {}
result = {
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "scope": (
        "Trivy 0.58.1 local-image vulnerability scan; HIGH/CRITICAL, "
        "fixable findings only; application not started"
    ),
    "requested_image": args.image,
    "image_id": metadata.get("ImageID"),
    "os": metadata.get("OS"),
    "finding_count": len(findings),
    "findings": findings,
    "passed": not findings,
}
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
