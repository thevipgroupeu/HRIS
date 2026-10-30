from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


def _digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "")
    trusted_proxy_count = getattr(settings, "CAREERHUB_TRUSTED_PROXY_COUNT", 0)
    if trusted_proxy_count:
        forwarded = [
            part.strip()
            for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= trusted_proxy_count:
            remote = forwarded[-trusted_proxy_count]
    try:
        return str(ipaddress.ip_address(remote))
    except ValueError:
        return "unknown"


def _cache_key(scope, discriminator):
    return f"careerhub-auth:{scope}:{_digest(discriminator)}"


def consume_rate(scope, discriminator):
    limit, window = settings.CAREERHUB_AUTH_RATE_LIMITS[scope]
    key = _cache_key(scope, discriminator)
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=window)
            count = 1
    return RateLimitResult(allowed=count <= limit, retry_after=window)


def reset_rate(scope, discriminator):
    cache.delete(_cache_key(scope, discriminator))


def fingerprint(value, *, namespace):
    if not value:
        return ""
    return _digest(f"{namespace}:{str(value)[:512]}")
