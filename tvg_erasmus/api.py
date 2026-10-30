import secrets
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from tvg_careers.models import Application
from tvg_erasmus.services import submit_evidence, verification_projection


def _authorized(request):
    if not getattr(settings, "CAREERHUB_VERIFICATION_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_VERIFICATION_API_TOKEN", "")
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


@csrf_exempt
@never_cache
@require_POST
def upload_verification_evidence(request, application_id, requirement_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        person_public_id = uuid.UUID(str(request.POST["person_public_id"]))
        upload = request.FILES["document"]
    except (KeyError, TypeError, ValueError):
        return _json({"detail": "Invalid verification upload."}, status=400)
    employer_id = getattr(settings, "TVG_LEGAL_EMPLOYER_ID", None)
    application = (
        Application.all_objects.select_related("person", "opening__venture")
        .filter(
            public_id=application_id,
            opening__venture__legal_employer_id=employer_id,
            is_active=True,
        )
        .first()
    )
    if not application:
        return _json({"detail": "Not found."}, status=404)
    try:
        evidence = submit_evidence(
            application=application,
            requirement_public_id=requirement_id,
            person_public_id=person_public_id,
            upload=upload,
        )
    except PermissionDenied:
        return _json({"detail": "Not found."}, status=404)
    except ValidationError:
        return _json({"detail": "Verification document was not accepted."}, status=400)
    return _json(
        {
            "evidence_id": str(evidence.public_id),
            "scan_status": evidence.scan_status,
            "verification": verification_projection(application),
        },
        status=201,
    )
