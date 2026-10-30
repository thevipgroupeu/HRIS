"""Synthetic nginx routing/header checks in one disposable, network-less container.

No Compose, deployed services, credentials, database, volumes or mail are used.
Requires the nginx:alpine image to be present locally. Reports only assertions,
never request/header contents. The existing server is not contacted or changed.
"""

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path)
args = parser.parse_args()
container = f"tvg-synthetic-proxy-{uuid4().hex[:12]}"
image = "nginx:alpine"
checks = []


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=check, timeout=30
    )


def request(port, host, path, headers=()):
    command = [
        "exec",
        container,
        "wget",
        "-q",
        "-S",
        "-O",
        "-",
        "--header",
        f"Host: {host}",
    ]
    for header in headers:
        command += ["--header", header]
    command += [f"http://127.0.0.1:{port}{path}"]
    result = docker(*command, check=False)
    return result.stdout, result.stderr


created = False
try:
    digest = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
    config = (ROOT / "docker/nginx.conf").read_text(encoding="utf-8")
    for service, port in (("careerhub", 18001), ("peoplehub", 18002), ("web", 18003)):
        config = config.replace(f"server {service}:8000;", f"server 127.0.0.1:{port};")
    synthetic_upstreams = ""
    for service, port in (("careerhub", 18001), ("peoplehub", 18002)):
        synthetic_upstreams += f"""
    server {{
        listen 127.0.0.1:{port};
        location / {{
            return 200 '{service}|$http_host|$http_x_forwarded_proto|$http_x_forwarded_for';
        }}
    }}
"""
    before, after = config.rsplit("}", 1)
    config = before + synthetic_upstreams + "}" + after
    with tempfile.TemporaryDirectory(prefix="tvg-synthetic-proxy-") as tmp:
        path = Path(tmp) / "nginx.conf"
        path.write_text(config, encoding="utf-8")
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--label",
            "purpose=synthetic-proxy-test",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={path},target=/etc/nginx/nginx.conf,readonly",
            "--entrypoint",
            "nginx",
            image,
            "-g",
            "daemon off;",
        )
        created = True
        for _ in range(20):
            body, _ = request(80, "career.thevipgroup.eu", "/candidate/sign-in/")
            if body.startswith("careerhub|"):
                break
            time.sleep(0.1)
        for route in ("/candidate/sign-in/", "/candidate/create-account/"):
            body, _ = request(
                8080,
                "career.thevipgroup.eu",
                route,
                (
                    "X-Forwarded-Proto: https",
                    "CF-Connecting-IP: 192.0.2.10",
                    "X-Forwarded-For: 198.51.100.99",
                ),
            )
            checks.append(
                {
                    "check": f"careerhub-route-{route}",
                    "passed": body
                    == "careerhub|career.thevipgroup.eu|https|192.0.2.10",
                }
            )
        body, _ = request(
            8080,
            "people.thevipgroup.eu",
            "/login/",
            ("X-Forwarded-Proto: https", "CF-Connecting-IP: 192.0.2.11"),
        )
        checks.append(
            {
                "check": "peoplehub-login-route",
                "passed": body == "peoplehub|people.thevipgroup.eu|https|192.0.2.11",
            }
        )
        body, _ = request(
            80,
            "career.thevipgroup.eu",
            "/candidate/sign-in/",
            (
                "X-Forwarded-Proto: https",
                "CF-Connecting-IP: 192.0.2.10",
                "X-Forwarded-For: 198.51.100.99",
            ),
        )
        checks.append(
            {
                "check": "untrusted-listener-ignores-spoofed-headers",
                "passed": body == "careerhub|career.thevipgroup.eu|http|127.0.0.1",
            }
        )
        body, headers = request(8080, "unknown.example.invalid", "/")
        checks.append(
            {"check": "unknown-host-denied", "passed": "404 Not Found" in headers}
        )
        _, headers = request(
            8080, "people.thevipgroup.eu", "/internal/careerhub/v1/applications/"
        )
        checks.append(
            {"check": "private-bridge-not-public", "passed": "403 Forbidden" in headers}
        )
        canary = "synthetic-canary-" + uuid4().hex
        request(
            8080,
            "career.thevipgroup.eu",
            f"/candidate/verify/{canary}/",
            (f"Referer: https://example.invalid/{canary}",),
        )
        logs = docker("logs", container).stdout
        checks.append(
            {
                "check": "nginx-access-log-omits-token-path-and-referrer",
                "passed": canary not in logs and "status=200" in logs,
            }
        )
finally:
    if created:
        # Exact container created by this process, no volumes and no user data.
        docker("rm", "-f", container)

result = {
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "scope": "network-less synthetic upstreams; not live Django/Cloudflare",
    "image_id": digest,
    "checks": checks,
    "passed": all(item["passed"] for item in checks),
}
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
