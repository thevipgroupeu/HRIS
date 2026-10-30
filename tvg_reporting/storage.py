from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class ReportingPrivateStorage(FileSystemStorage):
    """Non-addressable storage; downloads always pass object authorization."""

    def __init__(self, location=None):
        location = location or getattr(
            settings,
            "TVG_REPORTING_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "reporting-private-media",
        )
        super().__init__(location=location, base_url=None)
