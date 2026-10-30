from django.conf import settings
from django.core.files.storage import FileSystemStorage


class OnboardingPrivateStorage(FileSystemStorage):
    """Private storage: documents are served only by authorized views."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "location",
            getattr(
                settings,
                "TVG_ONBOARDING_PRIVATE_MEDIA_ROOT",
                settings.BASE_DIR / "onboarding-private-media",
            ),
        )
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)
