from __future__ import annotations

import secrets

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from tvg_careers.models import JobOpening
from tvg_careers.public_catalog import get_public_opening, list_public_openings


def _authorized(request):
    if not getattr(settings, "CAREERHUB_INTERNAL_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_INTERNAL_API_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not expected or not supplied.startswith(prefix):
        return False
    return secrets.compare_digest(supplied[len(prefix) :], expected)


def _json(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _not_authorized():
    return _json({"detail": "Not found."}, status=404)


@never_cache
@require_GET
def public_job_list(request):
    if not _authorized(request):
        return _not_authorized()
    return _json(list_public_openings(request.GET))


@never_cache
@require_GET
def public_job_detail(request, venture_slug, opening_slug):
    if not _authorized(request):
        return _not_authorized()
    try:
        opening = get_public_opening(
            venture_slug=venture_slug,
            opening_slug=opening_slug,
        )
    except JobOpening.DoesNotExist:
        return _json({"detail": "Not found."}, status=404)
    return _json(opening)
