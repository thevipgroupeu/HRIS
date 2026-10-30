from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class CompletionPrivateStorage(FileSystemStorage):
    """Private completion evidence; every download requires object access."""

    def __init__(self, location=None):
        location = location or getattr(
            settings,
            "TVG_OFFBOARDING_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "offboarding-private-media",
        )
        super().__init__(location=location, base_url=None)
