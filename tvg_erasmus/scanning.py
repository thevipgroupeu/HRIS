from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class ScanResult:
    status: str
    reference: str = ""


class DisabledScanner:
    """Fail-safe default: evidence remains quarantined pending an approved scanner."""

    def scan(self, *, content, filename, content_type, sha256):
        return ScanResult(status="pending")


class FakeCleanScanner:
    """Deterministic local/test scanner that performs no external processing."""

    def scan(self, *, content, filename, content_type, sha256):
        if getattr(settings, "HORILLA_ENV", "").strip().lower() not in {
            "test",
            "development",
            "local",
        }:
            raise ImproperlyConfigured(
                "FakeCleanScanner is only permitted in explicit local/test environments"
            )
        return ScanResult(status="clean", reference=f"fake:{sha256[:12]}")


def scan_verification_upload(**kwargs):
    backend = getattr(
        settings,
        "TVG_VERIFICATION_SCANNER_BACKEND",
        "tvg_erasmus.scanning.DisabledScanner",
    )
    return import_string(backend)().scan(**kwargs)
