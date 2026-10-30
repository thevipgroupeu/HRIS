"""Narrow CareerHub command/download boundary for Phase 9 arrangements."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from tvg_erasmus.models import ErasmusCase
from tvg_erasmus.services import (
    arrangement_projection,
    candidate_acceptance_letter,
    confirm_acceptance_receipt,
    confirm_arrangement_dates,
)


def _authorized(request):
    if not getattr(settings, "CAREERHUB_ARRANGEMENT_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_ARRANGEMENT_API_TOKEN", "")
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


def _payload(request, *, dates=False):
    if len(request.body) > 16 * 1024:
        raise ValueError
    try:
        data = json.loads(request.body)
        payload = {
            "person_public_id": uuid.UUID(str(data["person_public_id"])),
        }
        if dates:
            payload.update(
                {
                    "start_date": date.fromisoformat(str(data["start_date"])),
                    "end_date": date.fromisoformat(str(data["end_date"])),
                }
            )
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError from exc


def _owned_case(case_id, person_public_id):
    return (
        ErasmusCase.all_objects.select_related("application__person")
        .filter(
            public_id=case_id,
            application__person__public_id=person_public_id,
            application__opening__venture__legal_employer_id=getattr(
                settings, "TVG_LEGAL_EMPLOYER_ID", None
            ),
            is_active=True,
        )
        .first()
    )


@csrf_exempt
@never_cache
@require_POST
def confirm_dates(request, case_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        data = _payload(request, dates=True)
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    case = _owned_case(case_id, data["person_public_id"])
    if not case:
        return _json({"detail": "Not found."}, status=404)
    try:
        case = confirm_arrangement_dates(case_id=case.public_id, **data)
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except ValidationError:
        return _json({"detail": "The dates could not be confirmed."}, status=409)
    return _json({"arrangement": arrangement_projection(case.application)})


@csrf_exempt
@never_cache
@require_POST
def confirm_receipt(request, case_id, letter_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        data = _payload(request)
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    case = _owned_case(case_id, data["person_public_id"])
    if not case:
        return _json({"detail": "Not found."}, status=404)
    try:
        confirm_acceptance_receipt(
            case_id=case.public_id,
            letter_id=letter_id,
            person_public_id=data["person_public_id"],
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except ValidationError:
        return _json({"detail": "Receipt could not be confirmed."}, status=409)
    return _json({"arrangement": arrangement_projection(case.application)})


@never_cache
@require_GET
def download_acceptance(request, case_id, letter_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        person_public_id = uuid.UUID(request.GET.get("person_public_id", ""))
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    case = _owned_case(case_id, person_public_id)
    if not case:
        return _json({"detail": "Not found."}, status=404)
    try:
        letter = candidate_acceptance_letter(
            case_id=case.public_id,
            letter_id=letter_id,
            person_public_id=person_public_id,
        )
        response = FileResponse(
            letter.pdf_file.open("rb"),
            as_attachment=True,
            filename=Path(
                f"acceptance-{letter.letter_number.replace('/', '-')}.pdf"
            ).name,
            content_type="application/pdf",
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except (FileNotFoundError, OSError):
        return _json({"detail": "Document unavailable."}, status=404)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
