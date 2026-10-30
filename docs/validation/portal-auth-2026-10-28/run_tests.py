"""Isolated portal runtime validation; refuses inherited DB/media/mail settings."""

import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
mode = sys.argv[1] if len(sys.argv) == 2 else ""
if mode not in {"public", "internal"}:
    raise SystemExit("Specify public or internal")
runtime = (
    "horilla.settings.careerhub" if mode == "public" else "horilla.settings.peoplehub"
)
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
        "NUMBER_OF_PROCESSORS",
    }
}
os.environ.clear()
os.environ.update(keep)
os.environ.update(
    DEBUG="True",
    HORILLA_ENV="test",
    DJANGO_SETTINGS_MODULE=runtime,
    SECRET_KEY="synthetic-isolated-portal-validation-only-" * 3,
    ALLOWED_HOSTS="testserver,people.thevipgroup.eu",
    DB_INIT_PASSWORD="synthetic-test-only",
    PEOPLEHUB_ALLOWED_HOSTS="testserver,people.thevipgroup.eu",
    CAREERHUB_ALLOWED_HOSTS="testserver,career.thevipgroup.eu",
)
import environ

environ.Env.read_env = lambda *args, **kwargs: None


class NoMigrations:
    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return None


with tempfile.TemporaryDirectory(prefix="synthetic-portal-validation-") as tmp:
    os.environ.update(
        CAREERHUB_SQLITE_PATH=":memory:",
        CAREERHUB_PRIVATE_MEDIA_ROOT=tmp,
        CAREERHUB_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CAREERHUB_PRIVACY_NOTICE_VERSION="synthetic-validation-only",
    )
    from django.conf import settings

    settings.DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.MEDIA_ROOT = tmp
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    for prefix in (
        "VERIFICATION",
        "ACCEPTANCE",
        "ERASMUS_DOCUMENT",
        "ONBOARDING",
        "REPORTING",
        "OFFBOARDING",
    ):
        setattr(settings, f"TVG_{prefix}_PRIVATE_MEDIA_ROOT", tmp)
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.SENTRY_DSN = ""
    if mode == "internal":
        settings.MIGRATION_MODULES = NoMigrations()
    capture = io.StringIO()
    exit_code = 0
    exception = None
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        try:
            import django

            django.setup()
            from django.core.management import call_command

            label = (
                "tvg_careers.accounts.tests"
                if mode == "public"
                else "tvg_peoplehub.tests"
            )
            call_command("test", label, interactive=False, verbosity=1)
        except SystemExit as exc:
            exit_code = exc.code or 0
        except Exception as exc:
            exit_code = 1
            exception = type(exc).__name__
    # Do not persist raw logs: they can include auth URL tokens.
    prefixes = (
        "Found ",
        "Ran ",
        "OK",
        "FAILED",
        "FAIL:",
        "ERROR:",
        "System check identified",
    )
    summary = [
        line for line in capture.getvalue().splitlines() if line.startswith(prefixes)
    ]
    result = {
        "runtime": runtime,
        "database": "temporary in-memory SQLite",
        "email": "local-memory only",
        "exit_code": exit_code,
        "summary": summary,
        "exception_type": exception,
        "python": sys.version.split()[0],
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "migrations": (
            "enabled" if mode == "public" else "disabled for focused auth tests"
        ),
        "password_hashing": "test-only MD5; not a production security/capacity check",
    }
    Path(__file__).with_name(f"results-{mode}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    if exit_code:
        for line in capture.getvalue().splitlines():
            if line.startswith(
                ("  File ", "AssertionError:", "django.urls.exceptions.NoReverseMatch:")
            ):
                print(line)
    raise SystemExit(exit_code)
