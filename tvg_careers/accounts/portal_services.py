from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from tvg_careers.accounts.application_client import get_application_client
from tvg_careers.accounts.models import (
    CandidateApplication,
    CandidateApplicationStatusEvent,
    CandidateProfile,
    CandidateProfileDocument,
)


@dataclass(frozen=True)
class DocumentContent:
    original_name: str
    content: bytes
    sha256: str
    content_type: str


def profile_snapshot(account, profile):
    def date(value):
        return value.isoformat() if value else None

    return {
        "name": account.get_full_name(),
        "email": account.email,
        "privacy_notice_version": account.privacy_notice_version,
        "privacy_notice_acknowledged_at": (
            account.privacy_notice_acknowledged_at.isoformat()
        ),
        "phone_number": profile.phone_number,
        "address": profile.address_line,
        "city": profile.city,
        "postal_code": profile.postal_code,
        "country_of_residence": profile.country_of_residence,
        "university": profile.university,
        "degree_programme": profile.degree_programme,
        "field_of_study": profile.field_of_study,
        "expected_graduation": date(profile.expected_graduation),
        "languages": list(profile.languages),
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "preferred_start_date": date(profile.preferred_start_date),
        "preferred_end_date": date(profile.preferred_end_date),
        "preferred_duration_weeks": profile.preferred_duration_weeks,
        "erasmus_declaration": profile.erasmus_declaration,
    }


def read_document(document):
    with document.file.open("rb") as source:
        content = source.read()
    return DocumentContent(
        original_name=Path(document.original_name).name,
        content=content,
        sha256=document.sha256,
        content_type=document.content_type,
    )


def read_application_snapshot(
    application, field_name, original_name, sha256, content_type
):
    field = getattr(application, field_name)
    if not field:
        return None
    with field.open("rb") as source:
        content = source.read()
    return DocumentContent(
        original_name=Path(original_name).name,
        content=content,
        sha256=sha256,
        content_type=content_type,
    )


def freeze_application_snapshot(
    *, application, account, profile, questionnaire, questionnaire_version
):
    """Persist the submission evidence once, before crossing the service boundary."""
    with transaction.atomic():
        locked = CandidateApplication.objects.select_for_update(of=("self",)).get(pk=application.pk)
        if locked.state != CandidateApplication.State.DRAFT:
            return locked
        if locked.profile_snapshot:
            return locked

        current_documents = {
            document.kind: document
            for document in CandidateProfileDocument.objects.filter(
                account=account,
                is_current=True,
            )
        }
        cv_model = current_documents.get(CandidateProfileDocument.Kind.CV)
        if not cv_model:
            raise ValueError("A current CV is required.")
        cv = read_document(cv_model)
        cover_model = current_documents.get(CandidateProfileDocument.Kind.COVER_LETTER)
        cover = read_document(cover_model) if cover_model else None

        locked.profile_snapshot = profile_snapshot(account, profile)
        locked.questionnaire_snapshot = questionnaire
        locked.questionnaire_version_public_id = questionnaire_version["id"]
        locked.questionnaire_version_number = questionnaire_version["number"]
        locked.questionnaire_fingerprint = questionnaire_version["fingerprint"]
        locked.cv_original_name = cv.original_name
        locked.cv_sha256 = cv.sha256
        locked.cv_content_type = cv.content_type
        locked.cv_snapshot.save(cv.original_name, ContentFile(cv.content), save=False)
        if cover:
            locked.cover_letter_original_name = cover.original_name
            locked.cover_letter_sha256 = cover.sha256
            locked.cover_letter_content_type = cover.content_type
            locked.cover_letter_snapshot.save(
                cover.original_name,
                ContentFile(cover.content),
                save=False,
            )
        locked.save()
        return locked


def submit_application(
    *, application, account, profile, questionnaire, questionnaire_version
):
    application = freeze_application_snapshot(
        application=application,
        account=account,
        profile=profile,
        questionnaire=questionnaire,
        questionnaire_version=questionnaire_version,
    )
    if application.state != CandidateApplication.State.DRAFT:
        return application

    cv = read_application_snapshot(
        application,
        "cv_snapshot",
        application.cv_original_name,
        application.cv_sha256,
        application.cv_content_type,
    )
    if not cv:
        raise ValueError("The application CV snapshot is missing.")
    cover = read_application_snapshot(
        application,
        "cover_letter_snapshot",
        application.cover_letter_original_name,
        application.cover_letter_sha256,
        application.cover_letter_content_type,
    )
    result = get_application_client().submit(
        application=application,
        account=account,
        profile=application.profile_snapshot,
        questionnaire=application.questionnaire_snapshot,
        cv=cv,
        cover_letter=cover,
    )
    status = result.get("status") or {}
    now = timezone.now()
    with transaction.atomic():
        locked = CandidateApplication.objects.select_for_update(of=("self",)).get(pk=application.pk)
        if locked.state != CandidateApplication.State.DRAFT:
            return locked
        locked.state = CandidateApplication.State.SUBMITTED
        locked.friendly_status_code = status.get("code", "under_review")
        locked.friendly_status_label = status.get("label", "Under review")
        locked.submitted_at = now
        locked.status_updated_at = now
        locked.save()
        CandidateApplicationStatusEvent.objects.get_or_create(
            application=locked,
            code=locked.friendly_status_code,
            label=locked.friendly_status_label,
            occurred_at=now,
        )
    return locked


def sync_candidate_dashboard(account, *, candidate_timezone=None):
    results = get_application_client().statuses(
        person_public_id=account.person_public_id,
        candidate_timezone=candidate_timezone,
    )
    by_id = {str(item.get("application_id")): item for item in results}
    with transaction.atomic():
        for application in CandidateApplication.objects.select_for_update(of=("self",)).filter(
            account=account
        ):
            item = by_id.get(str(application.pk))
            if not item:
                continue
            status = item.get("status") or {}
            code = str(status.get("code", application.friendly_status_code))[:32]
            label = str(status.get("label", application.friendly_status_label))[:80]
            if code != application.friendly_status_code:
                occurred_at = timezone.now()
                history = item.get("history") or []
                matching = [event for event in history if event.get("code") == code]
                if matching:
                    try:
                        occurred_at = datetime.fromisoformat(
                            matching[-1]["occurred_at"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                CandidateApplicationStatusEvent.objects.get_or_create(
                    application=application,
                    code=code,
                    occurred_at=occurred_at,
                    defaults={"label": label},
                )
            application.friendly_status_code = code
            application.friendly_status_label = label
            application.status_updated_at = timezone.now()
            if (
                code == "withdrawn"
                and application.state != CandidateApplication.State.WITHDRAWN
            ):
                application.state = CandidateApplication.State.WITHDRAWN
                application.withdrawn_at = timezone.now()
            application.save(
                update_fields=(
                    "friendly_status_code",
                    "friendly_status_label",
                    "status_updated_at",
                    "state",
                    "withdrawn_at",
                    "updated_at",
                )
            )
    return by_id
