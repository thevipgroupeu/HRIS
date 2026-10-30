"""Private CareerHub-to-PeopleOps application command and status boundary."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from onboarding.models import CandidateStage, CandidateTask
from recruitment.models import (
    Candidate,
    CandidateDocument,
    RecruitmentSurveyAnswer,
    Stage,
)
from tvg_careers.attribution import APPLICATION_SOURCE_VALUES, normalize_attribution
from tvg_careers.document_security import inspect_candidate_document
from tvg_careers.models import Application, JobOpening, RecruitmentQuestionnaireVersion
from tvg_careers.questionnaires import (
    QuestionnaireConfigurationError,
    enqueue_application_confirmation,
    ensure_questionnaire_version,
)
from tvg_core.models import Person


def _authorized(request):
    if not getattr(settings, "CAREERHUB_APPLICATION_API_ENABLED", True):
        return False
    expected = getattr(settings, "CAREERHUB_APPLICATION_API_TOKEN", "")
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


def _friendly_status(application):
    candidate = application.candidate
    if application.withdrawn_at:
        return "withdrawn", "Withdrawn"
    if candidate.offer_letter_status == "accepted":
        return "offer_accepted", "Offer accepted"
    if candidate.offer_letter_status == "sent":
        return "offer_available", "Offer available"
    if candidate.offer_letter_status == "joined" or candidate.hired:
        return "selected", "Selected"
    if candidate.canceled or (
        candidate.stage_id and candidate.stage_id.stage_type == "cancelled"
    ):
        return "unsuccessful", "Application unsuccessful"
    stage_type = candidate.stage_id.stage_type if candidate.stage_id else "applied"
    return {
        "test": ("assessment", "Assessment"),
        "interview": ("interview", "Interview stage"),
        "hired": ("selected", "Selected"),
    }.get(stage_type, ("under_review", "Under review"))


def _safe_history(application):
    candidate = application.candidate
    rows = []
    try:
        historical = list(candidate.history.order_by("history_date"))
    except (AttributeError, TypeError):
        historical = []
    stage_ids = {row.stage_id_id for row in historical if row.stage_id_id}
    stages = Stage._base_manager.in_bulk(stage_ids)
    previous_code = None
    for row in historical:
        stage = stages.get(row.stage_id_id)
        stage_type = stage.stage_type if stage else "applied"
        code, label = {
            "test": ("assessment", "Assessment"),
            "interview": ("interview", "Interview stage"),
            "cancelled": ("unsuccessful", "Application unsuccessful"),
            "hired": ("selected", "Selected"),
        }.get(stage_type, ("under_review", "Under review"))
        if code != previous_code:
            rows.append(
                {
                    "code": code,
                    "label": label,
                    "occurred_at": row.history_date.isoformat(),
                }
            )
            previous_code = code
    if not rows:
        rows.append(
            {
                "code": "under_review",
                "label": "Under review",
                "occurred_at": application.submitted_at.isoformat(),
            }
        )
    if application.withdrawn_at:
        rows.append(
            {
                "code": "withdrawn",
                "label": "Withdrawn",
                "occurred_at": application.withdrawn_at.isoformat(),
            }
        )
    return rows


def _safe_projection(application, *, candidate_timezone=None):
    candidate = application.candidate
    code, label = _friendly_status(application)
    from tvg_interviews.services import interview_projection

    interviews = interview_projection(
        application,
        candidate_timezone=candidate_timezone,
    )
    offer_labels = {
        "sent": "Offer available",
        "accepted": "Offer accepted",
        "rejected": "Offer declined",
        "joined": "Offer completed",
    }
    offer = None
    if candidate.offer_letter_status in offer_labels:
        offer = {"status": offer_labels[candidate.offer_letter_status]}

    onboarding = {"status": "Not started", "completed_tasks": 0, "total_tasks": 0}
    candidate_stage = CandidateStage._base_manager.filter(
        candidate_id=candidate
    ).first()
    if candidate_stage:
        tasks = CandidateTask._base_manager.filter(candidate_id=candidate)
        total = tasks.count()
        completed = tasks.filter(status="done").count()
        onboarding = {
            "status": (
                "Complete" if candidate_stage.onboarding_end_date else "In progress"
            ),
            "completed_tasks": completed,
            "total_tasks": total,
        }
    from tvg_erasmus.document_services import document_projection
    from tvg_erasmus.services import arrangement_projection, verification_projection

    return {
        "application_id": str(application.public_id),
        "opening_public_id": str(application.opening.public_id),
        "status": {"code": code, "label": label},
        "history": _safe_history(application),
        "interviews": interviews,
        "offer": offer,
        "onboarding": onboarding,
        "verification": verification_projection(application),
        "arrangement": arrangement_projection(application),
        "documentation": document_projection(application),
    }


def _payload_from_request(request):
    raw = request.POST.get("payload", "")
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError
    try:
        payload = json.loads(raw)
        payload["application_id"] = uuid.UUID(str(payload["application_id"]))
        payload["person_public_id"] = uuid.UUID(str(payload["person_public_id"]))
        payload["opening_public_id"] = uuid.UUID(str(payload["opening_public_id"]))
        payload["email"] = str(payload["email"]).strip().casefold()
        if not isinstance(payload["profile"], dict):
            raise ValueError
        version = payload["questionnaire_version"]
        if not isinstance(version, dict):
            raise ValueError
        payload["questionnaire_version"] = {
            "id": uuid.UUID(str(version["id"])),
            "number": int(version["number"]),
            "fingerprint": str(version["fingerprint"]),
        }
        if payload["questionnaire_version"]["number"] < 1 or not re.fullmatch(
            r"[0-9a-f]{64}", payload["questionnaire_version"]["fingerprint"]
        ):
            raise ValueError
        if not isinstance(payload.get("questionnaire", []), list):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError from exc
    name = str(payload["profile"].get("name", "")).strip()
    phone = str(payload["profile"].get("phone_number", "")).strip()
    if not name or len(name) > 100:
        raise ValueError
    if phone and not re.fullmatch(r"\+?[0-9 ]{3,15}", phone):
        raise ValueError
    raw_source = str(payload.get("source", "company_website")).strip().casefold()
    if raw_source not in APPLICATION_SOURCE_VALUES:
        raise ValueError
    raw_utm = payload.get("utm_parameters", {})
    if not isinstance(raw_utm, dict):
        raise ValueError
    attribution = normalize_attribution({"source": raw_source, **raw_utm})
    payload["source"] = attribution["source"]
    payload["utm_parameters"] = attribution["utm_parameters"]
    return payload


def _questionnaire_answers(version, submitted):
    questions = {
        str(question["public_id"]): question for question in version.definition_snapshot
    }
    supplied = {}
    for item in submitted:
        question_id = str(item.get("id", "")) if isinstance(item, dict) else ""
        if question_id not in questions or question_id in supplied:
            raise ValueError
        supplied[question_id] = item.get("answer")

    answer_json = {}
    snapshot = []
    for question_id, question in questions.items():
        answer = supplied.get(question_id)
        empty = answer in (None, "", [])
        if question["required"] and empty:
            raise ValueError
        question_type = question["type"]
        options = question.get("options", [])
        if empty:
            normalized = None
        elif question_type == "multiple":
            if not isinstance(answer, list) or any(
                value not in options for value in answer
            ):
                raise ValueError
            normalized = [str(value)[:255] for value in answer]
        elif question_type == "checkbox":
            if not isinstance(answer, bool):
                raise ValueError
            normalized = answer
        else:
            if isinstance(answer, (dict, list)):
                raise ValueError
            normalized = str(answer).strip()[:4000]
            if question["required"] and not normalized:
                raise ValueError
            if question_type == "options" and normalized not in options:
                raise ValueError
            if question_type in {"number", "percentage", "rating"}:
                try:
                    number = Decimal(normalized)
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError from exc
                if question_type == "percentage" and not 0 <= number <= 100:
                    raise ValueError
                if question_type == "rating" and not 1 <= number <= 5:
                    raise ValueError
                if question_type == "rating" and number != number.to_integral_value():
                    raise ValueError
            if question_type == "date":
                try:
                    date.fromisoformat(normalized)
                except ValueError as exc:
                    raise ValueError from exc
        if normalized is not None:
            native_answer = (
                "Yes"
                if normalized is True
                else "No" if normalized is False else normalized
            )
            answer_json[question["label"]] = (
                native_answer if isinstance(native_answer, list) else [native_answer]
            )
        snapshot.append(
            {
                "id": question_id,
                "question": question["label"],
                "type": question_type,
                "required": question["required"],
                "options": list(options),
                "answer": normalized,
            }
        )
    return answer_json, snapshot


@csrf_exempt
@never_cache
@require_POST
def submit_application(request):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        payload = _payload_from_request(request)
        cv_upload = request.FILES["cv"]
        inspect_candidate_document(cv_upload)
        cover_upload = request.FILES.get("cover_letter")
        if cover_upload:
            inspect_candidate_document(cover_upload)
    except (KeyError, ValueError, ValidationError):
        return _json({"detail": "Invalid application."}, status=400)

    employer_id = getattr(settings, "TVG_LEGAL_EMPLOYER_ID", None)
    person = Person.all_objects.filter(
        public_id=payload["person_public_id"],
        legal_employer_id=employer_id,
        primary_email__iexact=payload["email"],
        is_active=True,
    ).first()
    opening = (
        JobOpening.all_objects.select_related(
            "recruitment", "recruitment__job_position_id", "venture"
        )
        .filter(
            public_id=payload["opening_public_id"],
            venture__legal_employer_id=employer_id,
            is_active=True,
        )
        .first()
    )
    if not person or not opening:
        return _json({"detail": "Application target not found."}, status=404)
    existing = Application.all_objects.filter(
        public_id=payload["application_id"]
    ).first()
    if existing:
        if existing.person_id == person.pk and existing.opening_id == opening.pk:
            projection = _safe_projection(existing)
            event = enqueue_application_confirmation(existing)
            projection["confirmation_event_id"] = str(event.event_id)
            return _json(projection)
        return _json({"detail": "Application identifier conflict."}, status=409)
    if not opening.is_publicly_available():
        return _json(
            {"detail": "This opening is not accepting applications."}, status=409
        )
    if Application.all_objects.filter(person=person, opening=opening).exists():
        return _json(
            {"detail": "An application already exists for this opening."}, status=409
        )

    version_data = payload["questionnaire_version"]
    questionnaire_version = RecruitmentQuestionnaireVersion.all_objects.filter(
        public_id=version_data["id"],
        recruitment=opening.recruitment,
        version_number=version_data["number"],
        fingerprint=version_data["fingerprint"],
    ).first()
    if not questionnaire_version:
        return _json({"detail": "Questionnaire version is invalid."}, status=409)
    try:
        current_version = ensure_questionnaire_version(opening.recruitment)
    except QuestionnaireConfigurationError:
        return _json({"detail": "Questionnaire is unavailable."}, status=409)
    if current_version.pk != questionnaire_version.pk:
        return _json(
            {"detail": "Questionnaire changed; reload before submitting."},
            status=409,
        )
    try:
        answer_json, questionnaire_snapshot = _questionnaire_answers(
            questionnaire_version, payload.get("questionnaire", [])
        )
        profile = payload["profile"]
        with transaction.atomic():
            stage = opening.recruitment.stage_set.filter(stage_type="applied").first()
            if not stage:
                stage = opening.recruitment.stage_set.order_by("sequence", "pk").first()
            candidate = Candidate(
                name=str(profile.get("name", ""))[:100],
                email=payload["email"],
                mobile=str(profile.get("phone_number", ""))[:15],
                address=str(profile.get("address", ""))[:255],
                country=str(profile.get("country_of_residence", ""))[:30],
                city=str(profile.get("city", ""))[:30],
                portfolio=str(profile.get("portfolio_url", ""))[:200],
                recruitment_id=opening.recruitment,
                job_position_id=opening.job_position,
                stage_id=stage,
                source="application",
                referral_source={
                    "company_website": "company_career_site",
                    "linkedin": "social_media",
                    "erasmusintern": "job_board",
                    "job_board": "job_board",
                    "university_referral": "college_university",
                    "referral": "employee_referral",
                }[payload["source"]],
                referral_source_other={
                    "linkedin": "LinkedIn",
                    "erasmusintern": "ErasmusIntern",
                }.get(payload["source"]),
                resume=cv_upload,
            )
            candidate.save()
            application = Application(
                public_id=payload["application_id"],
                person=person,
                candidate=candidate,
                opening=opening,
                questionnaire_version=questionnaire_version,
                questionnaire_snapshot=questionnaire_snapshot,
                source=payload["source"],
                utm_parameters=payload["utm_parameters"],
            )
            application.save()
            if answer_json:
                RecruitmentSurveyAnswer._base_manager.create(
                    candidate_id=candidate,
                    recruitment_id=opening.recruitment,
                    job_position_id=opening.job_position,
                    answer_json=json.dumps(answer_json),
                )
            if cover_upload:
                CandidateDocument._base_manager.create(
                    title="Application cover letter",
                    candidate_id=candidate,
                    document=cover_upload,
                )
            confirmation_event = enqueue_application_confirmation(application)
    except (IntegrityError, ValidationError, ValueError):
        return _json({"detail": "Application could not be created."}, status=409)

    projection = _safe_projection(application)
    projection["confirmation_event_id"] = str(confirmation_event.event_id)
    return _json(projection, status=201)


def _employer_applications():
    """A bridge token identifies one deployment, never every legal employer."""
    employer_id = getattr(settings, "TVG_LEGAL_EMPLOYER_ID", None)
    if not employer_id:
        return Application.all_objects.none()
    return Application.all_objects.filter(
        person__legal_employer_id=employer_id,
        person__is_active=True,
        opening__venture__legal_employer_id=employer_id,
        opening__recruitment__company_id=employer_id,
        candidate__recruitment_id__company_id=employer_id,
    )


@never_cache
@require_GET
def application_status(request):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        person_public_id = uuid.UUID(request.GET.get("person_public_id", ""))
    except ValueError:
        return _json({"detail": "Invalid request."}, status=400)
    applications = (
        _employer_applications()
        .filter(person__public_id=person_public_id)
        .select_related("candidate__stage_id", "opening")
        .order_by("-submitted_at")
    )
    candidate_timezone = request.GET.get("candidate_timezone") or None
    if candidate_timezone and len(candidate_timezone) > 64:
        return _json({"detail": "Invalid request."}, status=400)
    return _json(
        {
            "results": [
                _safe_projection(item, candidate_timezone=candidate_timezone)
                for item in applications
            ]
        }
    )


@csrf_exempt
@never_cache
@require_POST
def withdraw_application(request, application_id):
    if not _authorized(request):
        return _json({"detail": "Not found."}, status=404)
    try:
        payload = json.loads(request.body)
        person_public_id = uuid.UUID(str(payload["person_public_id"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _json({"detail": "Invalid request."}, status=400)
    with transaction.atomic():
        application = (
            _employer_applications()
            .select_for_update(of=("self",))
            .filter(public_id=application_id, person__public_id=person_public_id)
            .first()
        )
        if not application:
            return _json({"detail": "Application not found."}, status=404)
        if not application.withdrawn_at:
            application.withdrawn_at = timezone.now()
            application.save(update_fields=("withdrawn_at",))
            from tvg_erasmus.services import close_verification_for_inactive_application

            close_verification_for_inactive_application(
                application,
                reason="withdrawn",
            )
            candidate = application.candidate
            candidate.canceled = True
            candidate.save()
        return _json(_safe_projection(application))
