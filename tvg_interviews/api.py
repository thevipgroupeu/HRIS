from __future__ import annotations

import json
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from tvg_interviews.services import book_interview as book_interview_service
from tvg_interviews.services import interview_projection
from tvg_interviews.services import reschedule_interview as reschedule_interview_service


def _authorized(request):
    if not getattr(settings, "CAREERHUB_INTERVIEW_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_INTERVIEW_API_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    return bool(
        expected
        and supplied.startswith("Bearer ")
        and secrets.compare_digest(supplied[7:], expected)
    )


def _json(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _payload(request):
    try:
        data = json.loads(request.body)
        return {
            "person_public_id": uuid.UUID(str(data["person_public_id"])),
            "slot_id": uuid.UUID(str(data["slot_id"])),
            "candidate_timezone": str(data["candidate_timezone"]).strip(),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError from exc


@csrf_exempt
@never_cache
@require_POST
def book_interview(request, invitation_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        data = _payload(request)
        booking = book_interview_service(
            invitation_id=invitation_id,
            slot_id=data["slot_id"],
            person_public_id=data["person_public_id"],
            candidate_timezone=data["candidate_timezone"],
        )
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except (ImproperlyConfigured, ValidationError):
        return _json({"detail": "The interview could not be booked."}, status=409)
    return _json(
        {
            "booking_id": str(booking.public_id),
            "interviews": interview_projection(booking.invitation.application),
        },
        status=201,
    )


@csrf_exempt
@never_cache
@require_POST
def reschedule_interview(request, invitation_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        data = _payload(request)
        booking = reschedule_interview_service(
            invitation_id=invitation_id,
            slot_id=data["slot_id"],
            person_public_id=data["person_public_id"],
            candidate_timezone=data["candidate_timezone"],
        )
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except (ImproperlyConfigured, ValidationError):
        return _json({"detail": "The interview could not be rescheduled."}, status=409)
    return _json(
        {
            "booking_id": str(booking.public_id),
            "interviews": interview_projection(booking.invitation.application),
        }
    )
