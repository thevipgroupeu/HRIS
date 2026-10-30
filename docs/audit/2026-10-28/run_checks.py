"""Isolated audit runner. Never reads .env or uses deployed databases/media.

Run from the repository root with .venv/Scripts/python.exe. Generated evidence
contains only summaries; raw test logs (which can contain session tokens) are
not persisted. This is a diagnostic harness, not a runtime settings module.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument(
    "mode",
    choices=[
        "private",
        "public",
        "migrations",
        "probes",
        "native",
        "benchmark",
        "pg_migrations",
        "public_pg_migrations",
    ],
)
args = parser.parse_args()
# Keep only OS/runtime variables; inherited service credentials are excluded.
keep = {
    k: v
    for k, v in os.environ.items()
    if k.upper()
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
    SECRET_KEY="synthetic-audit-only-key-00000000000000000000000000000",
    ALLOWED_HOSTS="testserver,localhost,127.0.0.1",
    DB_INIT_PASSWORD="synthetic-audit-only",
    DJANGO_SETTINGS_MODULE=(
        "horilla.settings.careerhub"
        if args.mode in {"public", "public_pg_migrations"}
        else "horilla.settings.tvg_test"
    ),
)
import environ

environ.Env.read_env = lambda *a, **kw: None

with tempfile.TemporaryDirectory(prefix="tvg-audit-") as tmp:
    os.environ.update(
        CAREERHUB_SQLITE_PATH=":memory:",
        CAREERHUB_PRIVATE_MEDIA_ROOT=tmp,
        CAREERHUB_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CAREERHUB_PRIVACY_NOTICE_VERSION="synthetic-audit-2026-10",
    )
    from django.conf import settings

    settings.DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }
    if args.mode in {"pg_migrations", "public_pg_migrations"}:
        import subprocess

        label = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "purpose"}}',
                "tvg-audit-pg-20261028",
            ],
            text=True,
        ).strip()
        if label != "synthetic-readiness-audit":
            raise RuntimeError("Disposable audit database identity not verified")
        settings.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": (
                    "tvg_audit_careerhub"
                    if args.mode == "public_pg_migrations"
                    else "tvg_audit_django"
                ),
                "USER": "tvg_audit",
                "PASSWORD": "synthetic-audit-only-password",
                "HOST": "127.0.0.1",
                "PORT": "55479",
            }
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
    output = io.StringIO()
    started = time.perf_counter()
    failed = None
    exit_code = 0
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        try:
            import django

            django.setup()
            from django.core.management import call_command

            if args.mode in {"migrations", "pg_migrations", "public_pg_migrations"}:
                settings.MIGRATION_MODULES = {}
                call_command("check", verbosity=0)
                if args.mode != "public_pg_migrations":
                    call_command(
                        "check",
                        fail_level="WARNING",
                        tags=["horilla.tenancy"],
                        verbosity=0,
                    )
                try:
                    call_command(
                        "makemigrations", check=True, dry_run=True, verbosity=1
                    )
                except SystemExit as exc:
                    if args.mode != "public_pg_migrations" or not exc.code:
                        raise
                    exit_code = 1
                    print(
                        "AUDIT: CareerHub migration drift detected; release check FAILED. Applying only existing migrations to this disposable DB to test restore tooling, not approving the schema."
                    )
                call_command("migrate", interactive=False, verbosity=0)
                if args.mode == "pg_migrations":
                    from horilla.testkit import make_company
                    from tvg_careers.tests.factories import (
                        make_application,
                        make_job_opening,
                    )
                    from tvg_core.tests.factories import make_person, make_venture

                    company = make_company("Synthetic PostgreSQL Restore Employer")
                    person = make_person(
                        company=company, email="restore-audit@example.invalid"
                    )
                    for venture in ("FluxOps", "FindMyStay"):
                        opening = make_job_opening(
                            company=company,
                            venture=make_venture(company=company, name=venture),
                        )
                        make_application(opening=opening, person=person)
                    print(
                        "AUDIT: full PeopleOps migration graph applied on disposable PostgreSQL; one fictional Person with two linked native Candidates/Applications created"
                    )
                elif args.mode == "public_pg_migrations":
                    from tvg_careers.accounts.models import CandidateAccount

                    CandidateAccount.objects.create_user(
                        email="restore-audit@example.invalid",
                        password="synthetic-audit-only-password",
                        first_name="Synthetic",
                        last_name="Restore Candidate",
                        privacy_notice_version="synthetic-audit-only",
                    )
                    print(
                        "AUDIT: isolated CareerHub migration graph applied on disposable PostgreSQL with one fictional account; no mail or identity provisioning"
                    )
            else:
                labels = (
                    ["tvg_careers.accounts.tests"]
                    if args.mode == "public"
                    else (
                        ["audit_probes"]
                        if args.mode == "probes"
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
                )
                if args.mode == "native":
                    labels = [
                        "base.tests.test_protected_media_hidden_paths",
                        "base.tests.test_tenancy_check",
                        "base.tests.test_multi_tenancy",
                        "base.tests.test_export_access",
                        "leave.tests.test_leave_tab_authz",
                        "helpdesk.tests.test_ticket_view_authz",
                        "horilla_api.tests.test_tenant_scoping",
                        "horilla_api.tests.test_document_api_authz",
                        "horilla_api.tests.test_directory_access",
                        "horilla_api.tests.test_attendance_mail_authz",
                    ]
                elif args.mode == "benchmark":
                    labels = ["audit_benchmark"]
                sys.path.insert(0, str(Path(__file__).parent))
                call_command("test", *labels, verbosity=1, interactive=False)
        except SystemExit as exc:
            exit_code = exc.code or 0
        except Exception as exc:
            exit_code = 1
            failed = f"{type(exc).__name__}: {exc}"
    raw = output.getvalue()
    # Summarize, exclude raw logs and any credential-bearing URL/session value.
    lines = [
        line[line.index("AUDIT:") :] if "AUDIT:" in line else line
        for line in raw.splitlines()
        if "AUDIT:" in line
        or line.startswith(
            (
                "Found ",
                "Ran ",
                "FAILED",
                "OK",
                "FAIL:",
                "ERROR:",
                "No changes",
                "System check identified",
            )
        )
    ]
    if args.mode in {"migrations", "pg_migrations", "public_pg_migrations"}:
        lines.extend(
            line
            for line in raw.splitlines()
            if line.startswith("Migrations for ")
            or line.strip().startswith(("+ ", "~ ", "- "))
        )
    result = {
        "mode": args.mode,
        "date": "2026-10-28",
        "python": sys.version.split()[0],
        "django": django.get_version(),
        "database": (
            "disposable PostgreSQL 16 (tvg_audit_careerhub)"
            if args.mode == "public_pg_migrations"
            else (
                "disposable PostgreSQL 16 (tvg_audit_django)"
                if args.mode == "pg_migrations"
                else "temporary in-memory SQLite"
            )
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "exit_code": exit_code,
        "summary": lines,
        "exception": failed,
    }
    Path(__file__).with_name(f"evidence-{args.mode}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    if exit_code:
        # Only assertion/exception context, not captured application logs.
        for chunk in raw.split(
            "======================================================================"
        )[1:]:
            print(
                chunk.split(
                    "----------------------------------------------------------------------"
                )[0][:1500]
            )
    sys.exit(exit_code)
