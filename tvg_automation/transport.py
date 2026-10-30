from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class TransportError(Exception):
    def __init__(self, message, *, code="transport_error", retryable=True, status=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class TransportResponse:
    status: int
    external_execution_id: str = ""


class DisabledActivepiecesTransport:
    def send(self, body: bytes, headers: dict[str, str]) -> TransportResponse:
        raise TransportError(
            "Activepieces transport is disabled.",
            code="transport_disabled",
            retryable=False,
        )


class ActivepiecesWebhookTransport:
    def send(self, body: bytes, headers: dict[str, str]) -> TransportResponse:
        url = getattr(settings, "TVG_ACTIVEPIECES_WEBHOOK_URL", "")
        if not url:
            raise TransportError(
                "Activepieces webhook URL is not configured.",
                code="configuration_missing",
                retryable=False,
            )
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(
                request, timeout=15
            ) as response:  # noqa: S310 - configured URL
                response_body = response.read(16 * 1024)
                status = response.status
        except HTTPError as exc:
            raise TransportError(
                f"Activepieces returned HTTP {exc.code}.",
                code="http_error",
                retryable=exc.code >= 500 or exc.code == 429,
                status=exc.code,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise TransportError(
                "Activepieces could not be reached.", code="network_error"
            ) from exc
        if status < 200 or status >= 300:
            raise TransportError(
                f"Activepieces returned HTTP {status}.",
                code="http_error",
                retryable=status >= 500 or status == 429,
                status=status,
            )
        execution_id = ""
        try:
            payload = json.loads(response_body or b"{}")
            execution_id = str(payload.get("execution_id", ""))[:160]
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            pass
        return TransportResponse(status=status, external_execution_id=execution_id)


class SyntheticActivepiecesTransport:
    """Network-free test/staging sink selected only by explicit settings."""

    deliveries: list[dict] = []
    failures_remaining = 0

    @classmethod
    def reset(cls):
        cls.deliveries.clear()
        cls.failures_remaining = 0

    def send(self, body: bytes, headers: dict[str, str]) -> TransportResponse:
        if self.__class__.failures_remaining:
            self.__class__.failures_remaining -= 1
            raise TransportError(
                "Synthetic transient failure.", code="synthetic_failure"
            )
        payload = json.loads(body)
        self.__class__.deliveries.append({"payload": payload, "headers": dict(headers)})
        return TransportResponse(
            status=202,
            external_execution_id=f"synthetic-{payload['event_id']}",
        )
