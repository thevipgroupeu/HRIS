from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


class TrainingProviderError(Exception):
    """A provider failure safe to record without leaking credentials or payloads."""


@dataclass(frozen=True)
class MoodleUserRequest:
    staff_reference: str
    company_reference: str
    company_email: str
    first_name: str
    last_name: str
    idempotency_key: str


@dataclass(frozen=True)
class MoodleAccountResult:
    external_user_id: str
    active: bool = True


@dataclass(frozen=True)
class MoodleEnrollmentRequest:
    external_user_id: str
    external_course_id: str
    due_date: date
    idempotency_key: str


@dataclass(frozen=True)
class MoodleEnrollmentResult:
    external_enrollment_id: str
    launch_url: str = ""


@dataclass(frozen=True)
class MoodleProgressRequest:
    external_enrollment_id: str
    external_course_id: str


@dataclass(frozen=True)
class MoodleProgressResult:
    external_enrollment_id: str
    progress_percent: int
    completed_at: datetime | None = None


class TrainingProvider(Protocol):
    def ensure_account(self, request: MoodleUserRequest) -> MoodleAccountResult: ...

    def enroll(self, request: MoodleEnrollmentRequest) -> MoodleEnrollmentResult: ...

    def progress(
        self, requests: tuple[MoodleProgressRequest, ...]
    ) -> tuple[MoodleProgressResult, ...]: ...


class DisabledTrainingProvider:
    """Fail closed until an approved Activepieces/Moodle contract is configured."""

    def _unavailable(self):
        raise TrainingProviderError(
            "No authorized Moodle provisioning adapter is configured."
        )

    def ensure_account(self, request):
        self._unavailable()

    def enroll(self, request):
        self._unavailable()

    def progress(self, requests):
        self._unavailable()


class SyntheticTrainingProvider:
    """Deterministic, no-network adapter selected only by explicit test settings."""

    account_calls: list[MoodleUserRequest] = []
    enrollment_calls: list[MoodleEnrollmentRequest] = []
    progress_values: dict[str, MoodleProgressResult] = {}

    @classmethod
    def reset(cls):
        cls.account_calls.clear()
        cls.enrollment_calls.clear()
        cls.progress_values.clear()

    def ensure_account(self, request):
        self.__class__.account_calls.append(request)
        return MoodleAccountResult(
            external_user_id=f"synthetic-user-{request.staff_reference}"
        )

    def enroll(self, request):
        self.__class__.enrollment_calls.append(request)
        return MoodleEnrollmentResult(
            external_enrollment_id=(
                f"synthetic-enrollment-{request.external_user_id}-"
                f"{request.external_course_id}"
            ),
            launch_url=f"https://moodle.example.test/course/{request.external_course_id}",
        )

    def progress(self, requests):
        return tuple(
            self.__class__.progress_values.get(
                request.external_enrollment_id,
                MoodleProgressResult(
                    external_enrollment_id=request.external_enrollment_id,
                    progress_percent=0,
                ),
            )
            for request in requests
        )


def get_training_provider() -> TrainingProvider:
    provider = getattr(settings, "TVG_TRAINING_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_TRAINING_PROVIDER_BACKENDS", {})
    path = backends.get(provider)
    if not path:
        raise ImproperlyConfigured("Unknown TrainingHub provider.")
    environment = getattr(settings, "HORILLA_ENV", "") or getattr(
        settings, "TVG_AUTOMATION_ENVIRONMENT", "development"
    )
    if environment in {"staging", "production"} and "Synthetic" in path:
        raise ImproperlyConfigured(
            "Synthetic TrainingHub providers are forbidden outside development/test."
        )
    return import_string(path)()
