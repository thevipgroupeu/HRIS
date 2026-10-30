from __future__ import annotations

import json
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from base.models import Company
from tvg_core.models import Person


def _authorized(request):
    if not getattr(settings, "CAREERHUB_IDENTITY_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_IDENTITY_API_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    prefix = "Bearer "
    return bool(
        expected
        and supplied.startswith(prefix)
        and secrets.compare_digest(supplied[len(prefix) :], expected)
    )


def _json(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _validated_payload(request):
    if request.content_type != "application/json" or len(request.body) > 4096:
        raise ValueError
    try:
        payload = json.loads(request.body)
        uuid.UUID(str(payload["account_id"]))
        person_public_id = uuid.UUID(str(payload["person_public_id"]))
        email = str(payload["email"]).strip().casefold()
        preferred_name = str(payload.get("preferred_name", "")).strip()[:100]
        validate_email(email)
    except (
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError from exc
    return person_public_id, email, preferred_name


@csrf_exempt
@never_cache
@require_POST
def provision_person_identity(request):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        person_public_id, email, preferred_name = _validated_payload(request)
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)

    employer_id = getattr(settings, "TVG_LEGAL_EMPLOYER_ID", None)
    try:
        employer = Company.objects.get(pk=employer_id, is_active=True)
    except (Company.DoesNotExist, TypeError, ValueError):
        return _json({"detail": "Service unavailable."}, status=503)

    try:
        with transaction.atomic():
            existing_id = (
                Person.all_objects.select_for_update(of=("self",))
                .filter(public_id=person_public_id)
                .first()
            )
            if existing_id:
                if (
                    existing_id.legal_employer_id != employer.pk
                    or existing_id.primary_email.casefold() != email
                ):
                    return _json({"detail": "Identity review required."}, status=409)
                status = "linked"
                person = existing_id
            else:
                email_conflict = (
                    Person.all_objects.select_for_update(of=("self",))
                    .filter(legal_employer=employer, primary_email__iexact=email)
                    .exists()
                )
                if email_conflict:
                    return _json({"detail": "Identity review required."}, status=409)
                person = Person(
                    public_id=person_public_id,
                    legal_employer=employer,
                    primary_email=email,
                    preferred_name=preferred_name,
                )
                person.save()
                status = "created"
    except IntegrityError:
        return _json({"detail": "Identity review required."}, status=409)

    return _json(
        {"person_public_id": str(person.public_id), "status": status},
        status=200,
    )
