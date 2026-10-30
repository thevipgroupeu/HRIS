from __future__ import annotations

import hashlib
import hmac


def signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"v1={digest}"


def body_signature(secret: str, body: bytes) -> str:
    """Signature accepted by Activepieces' native HMAC webhook trigger."""

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_signature(secret: str, timestamp: str, body: bytes, supplied: str) -> bool:
    if not secret or not timestamp or not supplied:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), supplied)
