"""Owner-scoped CareerHub boundary for Erasmus document actions."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from tvg_erasmus.arrangement_api import _authorized, _json, _owned_case
from tvg_erasmus.document_services import (
    candidate_document_version,
    candidate_submit_document,
    document_projection,
    record_accommodation_response,
)
from tvg_erasmus.models import ErasmusCaseDocument


def _person_id(request):
    try:
        return uuid.UUID(str(request.POST.get("person_public_id", "")))
    except ValueError as exc:
        raise ValidationError("Invalid request.") from exc


@csrf_exempt
@never_cache
@require_POST
def upload_document(request, case_id, document_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        person_public_id = _person_id(request)
    except ValidationError:
        return _json({"detail": "Invalid request."}, status=400)
    case = _owned_case(case_id, person_public_id)
    document = ErasmusCaseDocument.all_objects.filter(
        public_id=document_id, case=case
    ).first()
    upload = request.FILES.get("document")
    if not case or not document:
        return _json({"detail": "Not found."}, status=404)
    if not upload:
        return _json({"detail": "A PDF or DOCX document is required."}, status=400)
    try:
        candidate_submit_document(
            document_id=document.public_id,
            person_public_id=person_public_id,
            upload=upload,
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except ValidationError:
        return _json({"detail": "The document could not be submitted."}, status=409)
    return _json({"documentation": document_projection(case.application)})


@never_cache
@require_GET
def download_document(request, case_id, document_id, version_id):
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
        version = candidate_document_version(
            document_id=document_id,
            version_id=version_id,
            person_public_id=person_public_id,
        )
        if version.document.case_id != case.pk:
            raise PermissionDenied
        response = FileResponse(
            version.file.open("rb"),
            as_attachment=True,
            filename=Path(version.original_filename).name,
            content_type=version.content_type,
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except (FileNotFoundError, OSError):
        return _json({"detail": "Document unavailable."}, status=404)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@csrf_exempt
@never_cache
@require_POST
def accommodation_response(request, case_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    if len(request.body) > 16 * 1024:
        return _json({"detail": "Invalid request."}, status=400)
    try:
        data = json.loads(request.body)
        person_public_id = uuid.UUID(str(data["person_public_id"]))
        if not isinstance(data["needs_assistance"], bool):
            raise ValueError
        needs_assistance = data["needs_assistance"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _json({"detail": "Invalid request."}, status=400)
    case = _owned_case(case_id, person_public_id)
    if not case:
        return _json({"detail": "Not found."}, status=404)
    try:
        record_accommodation_response(
            case_id=case.public_id,
            person_public_id=person_public_id,
            needs_assistance=needs_assistance,
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except ValidationError:
        return _json({"detail": "The response cannot be recorded yet."}, status=409)
    return _json({"documentation": document_projection(case.application)})
