from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class VerificationPrivateStorage(FileSystemStorage):
    """File storage with no public URL, outside Horilla's general media root."""

    def __init__(self, location=None):
        location = location or getattr(
            settings,
            "TVG_VERIFICATION_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "verification-private-media",
        )
        super().__init__(location=location, base_url=None)


@deconstructible
class AcceptancePrivateStorage(FileSystemStorage):
    """Private, non-addressable storage for issued acceptance-letter PDFs."""

    def __init__(self, location=None):
        location = location or getattr(
            settings,
            "TVG_ACCEPTANCE_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "acceptance-private-media",
        )
        super().__init__(location=location, base_url=None)


@deconstructible
class ErasmusDocumentPrivateStorage(FileSystemStorage):
    """Private storage for Erasmus templates and case-document versions."""

    def __init__(self, location=None):
        location = location or getattr(
            settings,
            "TVG_ERASMUS_DOCUMENT_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "erasmus-document-private-media",
        )
        super().__init__(location=location, base_url=None)
