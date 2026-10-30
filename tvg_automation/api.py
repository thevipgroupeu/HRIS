from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import timezone as datetime_timezone
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import LifecycleEvent, WebhookReceipt, WorkflowExecution
from .signing import verify_signature

TERMINAL = {
    WorkflowExecution.Status.SUCCEEDED,
    WorkflowExecution.Status.FAILED,
    WorkflowExecution.Status.CANCELLED,
}
TRANSITIONS = {
    WorkflowExecution.Status.ACCEPTED: {
        WorkflowExecution.Status.ACCEPTED,
        WorkflowExecution.Status.RUNNING,
        *TERMINAL,
    },
    WorkflowExecution.Status.RUNNING: {
        WorkflowExecution.Status.RUNNING,
        *TERMINAL,
    },
    WorkflowExecution.Status.SUCCEEDED: {WorkflowExecution.Status.SUCCEEDED},
    WorkflowExecution.Status.FAILED: {WorkflowExecution.Status.FAILED},
    WorkflowExecution.Status.CANCELLED: {WorkflowExecution.Status.CANCELLED},
}


def _response(payload, status=200):
    response = JsonResponse(payload, status=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _authenticated(request):
    timestamp = request.headers.get("X-TVG-Timestamp", "")
    supplied = request.headers.get("X-TVG-Signature", "")
    secret = getattr(settings, "TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET", "")
    if (
        not timestamp
        or len(timestamp) > 12
        or not timestamp.isascii()
        or not timestamp.isdecimal()
    ):
        return None
    try:
        requested_at = datetime.fromtimestamp(int(timestamp), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    max_skew = int(getattr(settings, "TVG_AUTOMATION_CALLBACK_MAX_SKEW_SECONDS", 300))
    if abs((timezone.now() - requested_at).total_seconds()) > max_skew:
        return None
    if not verify_signature(secret, timestamp, request.body, supplied):
        return None
    return requested_at


@csrf_exempt
@never_cache
@require_POST
def execution_callback(request):
    requested_at = _authenticated(request)
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if requested_at is None or not idempotency_key or len(idempotency_key) > 180:
        return _response({"detail": "Not found."}, status=404)
    body_hash = hashlib.sha256(request.body).hexdigest()
    existing = WebhookReceipt.all_objects.filter(
        idempotency_key=idempotency_key
    ).first()
    if existing:
        if existing.body_sha256 != body_hash:
            return _response({"detail": "Idempotency conflict."}, status=409)
        return _response(
            {"status": "already_processed"}, status=existing.response_status
        )
    try:
        payload = json.loads(request.body)
        if set(payload) - {
            "event_id",
            "workflow_key",
            "execution_id",
            "status",
            "message",
        }:
            raise ValueError
        event_id = UUID(str(payload["event_id"]))
        workflow_key = str(payload["workflow_key"])
        execution_id = str(payload["execution_id"]).strip()
        execution_status = str(payload["status"])
        message = str(payload.get("message", ""))[:500]
        if (
            not execution_id
            or len(execution_id) > 160
            or not workflow_key
            or len(workflow_key) > 80
            or execution_status not in WorkflowExecution.Status.values
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _response({"detail": "Invalid callback."}, status=400)

    response_status = 200
    try:
        with transaction.atomic():
            event = (
                LifecycleEvent.all_objects.select_for_update(of=("self",))
                .filter(
                    event_id=event_id,
                    workflow_key=workflow_key,
                    # A very fast flow can callback before the outbound HTTP
                    # request has returned and the dispatcher commits
                    # DELIVERED. PROCESSING is safe here because the callback
                    # is authenticated and can mutate only execution history.
                    status__in=(
                        LifecycleEvent.Status.PROCESSING,
                        LifecycleEvent.Status.DELIVERED,
                    ),
                )
                .first()
            )
            if not event:
                response_status = 404
            else:
                execution, _created = WorkflowExecution.all_objects.get_or_create(
                    event=event,
                    external_execution_id=execution_id,
                    defaults={
                        "workflow_key": workflow_key,
                        "status": WorkflowExecution.Status.ACCEPTED,
                        "started_at": timezone.now(),
                    },
                )
                if execution_status not in TRANSITIONS[execution.status]:
                    response_status = 409
                else:
                    execution.status = execution_status
                    execution.status_message = message
                    execution.completed_at = (
                        timezone.now() if execution_status in TERMINAL else None
                    )
                    execution.save(
                        update_fields=(
                            "status",
                            "status_message",
                            "completed_at",
                            "updated_at",
                        )
                    )
            WebhookReceipt.all_objects.create(
                idempotency_key=idempotency_key,
                event=event if response_status != 404 else None,
                request_timestamp=requested_at,
                body_sha256=body_hash,
                response_status=response_status,
            )
    except IntegrityError:
        receipt = WebhookReceipt.all_objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if not receipt:
            raise
        if receipt.body_sha256 != body_hash:
            return _response({"detail": "Idempotency conflict."}, status=409)
        response_status = receipt.response_status

    if response_status == 404:
        return _response({"detail": "Not found."}, status=404)
    if response_status == 409:
        return _response({"detail": "Invalid state transition."}, status=409)
    return _response({"status": "recorded"})
