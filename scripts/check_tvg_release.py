"""Local synthetic release checks: no .env, deployed DB/media or real mail.

Usage: python scripts/check_tvg_release.py private [--schema] [--labels ...]
       python scripts/check_tvg_release.py public [--schema]
Not a replacement for PostgreSQL/Redis staging or deployed-origin tests.
"""

import argparse
import contextlib
import importlib.abc
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runtime", choices=("public", "private"))
parser.add_argument("--schema", action="store_true")
parser.add_argument("--labels", nargs="+")
parser.add_argument("--report", type=Path)
parser.add_argument(
    "--dependency-overlay",
    type=Path,
    help="Isolated pip --target directory for testing upgraded pins without changing the installed runtime",
)
args = parser.parse_args()
if args.dependency_overlay:
    sys.path.insert(0, str(args.dependency_overlay.resolve(strict=True)))
keep = {
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
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "NUMBER_OF_PROCESSORS",
    }
}
os.environ.clear()
os.environ.update(keep)
os.environ.update(
    DEBUG="True",
    HORILLA_ENV="test",
    SECRET_KEY="synthetic-release-test-key-000000000000000000000000000000",
    DB_INIT_PASSWORD="synthetic-only",
    ALLOWED_HOSTS="testserver,localhost,127.0.0.1,people.thevipgroup.eu",
    CAREERHUB_ALLOWED_HOSTS="testserver,localhost,127.0.0.1,career.thevipgroup.eu",
    CAREERHUB_SQLITE_PATH=":memory:",
    CAREERHUB_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CAREERHUB_PRIVACY_NOTICE_VERSION="synthetic-test-only",
    DJANGO_SETTINGS_MODULE=(
        "horilla.settings.careerhub"
        if args.runtime == "public"
        else "horilla.settings.tvg_test"
    ),
)
import environ

environ.Env.read_env = lambda *a, **kw: None


class NoLocalOverrides(importlib.abc.MetaPathFinder):
    """Mirror Docker's exclusion of local_settings.py, without touching it."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "horilla.settings.local_settings":
            raise ModuleNotFoundError(
                "Local overrides excluded for isolated tests", name=fullname
            )
        return None


sys.meta_path.insert(0, NoLocalOverrides())
started = time.perf_counter()
captured = io.StringIO()
exit_code = 0
with tempfile.TemporaryDirectory(
    prefix="tvg-release-test-"
) as tmp, contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
    os.environ["CAREERHUB_PRIVATE_MEDIA_ROOT"] = tmp
    import django
    from django.conf import settings

    settings.DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.MEDIA_ROOT = tmp
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.SENTRY_DSN = ""
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    for name in (
        "VERIFICATION",
        "ACCEPTANCE",
        "ERASMUS_DOCUMENT",
        "ONBOARDING",
        "REPORTING",
        "OFFBOARDING",
    ):
        setattr(settings, f"TVG_{name}_PRIVATE_MEDIA_ROOT", tmp)
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    try:
        django.setup()
        from django.core.management import call_command

        call_command("check", verbosity=0)
        if args.runtime == "private":
            call_command(
                "check", fail_level="WARNING", tags=["horilla.tenancy"], verbosity=0
            )
        if args.schema:
            settings.MIGRATION_MODULES = {}
            call_command("makemigrations", check=True, dry_run=True, verbosity=1)
            if args.runtime == "public":
                call_command("migrate", interactive=False, verbosity=0)
                call_command(
                    "migrate",
                    "tvg_careerhub_accounts",
                    "0005",
                    interactive=False,
                    verbosity=0,
                )
                call_command("migrate", interactive=False, verbosity=0)
                print(
                    "PASS: Public migration graph applied; 0006 reversed and reapplied in temporary SQLite"
                )
        else:
            labels = args.labels or (
                ["tvg_careers.accounts.tests"]
                if args.runtime == "public"
                else [
                    "tvg_core",
                    "tvg_careers.tests",
                    "tvg_erasmus",
                    "tvg_interviews",
                    "tvg_onboarding",
                    "tvg_peoplehub",
                    "tvg_reporting",
                    "tvg_offboarding",
                    "tvg_automation",
                    "tvg_training",
                    "tvg_analytics",
                ]
            )
            call_command("test", *labels, verbosity=1, interactive=False)
    except SystemExit as exc:
        exit_code = exc.code or 0
    except Exception as exc:
        exit_code = 1
        print(f"ERROR: {type(exc).__name__}: {exc}")
raw = captured.getvalue()
summary = [
    line
    for line in raw.splitlines()
    if line.startswith(
        (
            "Found ",
            "Ran ",
            "OK",
            "FAILED",
            "FAIL:",
            "ERROR:",
            "PASS:",
            "No changes",
            "System check identified",
            "AssertionError:",
        )
    )
]
result = {
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "runtime": args.runtime,
    "schema": args.schema,
    "python": sys.version.split()[0],
    "django": django.get_version(),
    "database": "isolated in-memory SQLite",
    "mail": "local-memory only",
    "elapsed_seconds": round(time.perf_counter() - started, 2),
    "exit_code": exit_code,
    "summary": summary,
}
if args.runtime == "private":
    import rest_framework

    result["djangorestframework"] = rest_framework.VERSION
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
sys.exit(exit_code)
