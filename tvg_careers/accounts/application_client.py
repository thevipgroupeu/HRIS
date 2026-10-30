"""CareerHub client for the narrow PeopleOps application boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests
from django.conf import settings


class ApplicationServiceError(RuntimeError):
    pass


class ApplicationConflict(ApplicationServiceError):
    pass


@dataclass(frozen=True)
class AcceptanceDocument:
    content: bytes
    content_type: str = "application/pdf"


class PeopleOpsApplicationClient:
    def __init__(self, *, session=None):
        self.session = session or requests.Session()
        self.base_url = settings.CAREERHUB_PEOPLEOPS_API_URL.rstrip("/")
        self.token = settings.CAREERHUB_APPLICATION_API_TOKEN
        self.timeout = settings.CAREERHUB_API_TIMEOUT

    @property
    def headers(self):
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "THEVIPGROUP-CareerHub/1.0",
        }

    def _payload(self, response, *, expected=(200,)):
        if response.status_code == 409:
            raise ApplicationConflict
        if response.status_code not in expected:
            raise ApplicationServiceError
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApplicationServiceError from exc
        if not isinstance(payload, dict):
            raise ApplicationServiceError
        return payload

    def submit(
        self,
        *,
        application,
        account,
        profile,
        questionnaire,
        cv,
        cover_letter=None,
    ):
        payload = {
            "application_id": str(application.pk),
            "person_public_id": str(account.person_public_id),
            "opening_public_id": str(application.opening_public_id),
            "email": account.email,
            "profile": profile,
            "questionnaire_version": {
                "id": str(application.questionnaire_version_public_id),
                "number": application.questionnaire_version_number,
                "fingerprint": application.questionnaire_fingerprint,
            },
            "questionnaire": [
                {"id": item["id"], "answer": item.get("answer")}
                for item in questionnaire
            ],
            "source": application.source,
            "utm_parameters": application.utm_parameters,
        }
        files = {
            "cv": (cv.original_name, cv.content, cv.content_type),
        }
        if cover_letter:
            files["cover_letter"] = (
                cover_letter.original_name,
                cover_letter.content,
                cover_letter.content_type,
            )
        try:
            response = self.session.post(
                f"{self.base_url}/applications/",
                data={"payload": json.dumps(payload)},
                files=files,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response, expected=(200, 201))

    def statuses(self, *, person_public_id, candidate_timezone=None):
        try:
            response = self.session.get(
                f"{self.base_url}/applications/status/",
                params={
                    "person_public_id": str(person_public_id),
                    **(
                        {"candidate_timezone": candidate_timezone}
                        if candidate_timezone
                        else {}
                    ),
                },
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        payload = self._payload(response)
        if not isinstance(payload.get("results"), list):
            raise ApplicationServiceError
        return payload["results"]

    def withdraw(self, *, application_id, person_public_id):
        try:
            response = self.session.post(
                f"{self.base_url}/applications/{application_id}/withdraw/",
                json={"person_public_id": str(person_public_id)},
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response)

    def upload_verification(
        self, *, application_id, person_public_id, requirement_id, document
    ):
        headers = {
            **self.headers,
            "Authorization": f"Bearer {settings.CAREERHUB_VERIFICATION_API_TOKEN}",
        }
        try:
            response = self.session.post(
                f"{self.base_url}/applications/{application_id}/verification/"
                f"{requirement_id}/evidence/",
                data={"person_public_id": str(person_public_id)},
                files={
                    "document": (
                        document.name,
                        document.content,
                        document.content_type,
                    )
                },
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response, expected=(201,))

    def book_interview(
        self, *, invitation_id, slot_id, person_public_id, candidate_timezone
    ):
        return self._interview_command(
            action="book",
            invitation_id=invitation_id,
            slot_id=slot_id,
            person_public_id=person_public_id,
            candidate_timezone=candidate_timezone,
            expected=(201,),
        )

    def reschedule_interview(
        self, *, invitation_id, slot_id, person_public_id, candidate_timezone
    ):
        return self._interview_command(
            action="reschedule",
            invitation_id=invitation_id,
            slot_id=slot_id,
            person_public_id=person_public_id,
            candidate_timezone=candidate_timezone,
        )

    def _interview_command(
        self,
        *,
        action,
        invitation_id,
        slot_id,
        person_public_id,
        candidate_timezone,
        expected=(200,),
    ):
        headers = {
            **self.headers,
            "Authorization": f"Bearer {settings.CAREERHUB_INTERVIEW_API_TOKEN}",
        }
        try:
            response = self.session.post(
                f"{self.base_url}/interviews/{invitation_id}/{action}/",
                json={
                    "slot_id": str(slot_id),
                    "person_public_id": str(person_public_id),
                    "candidate_timezone": candidate_timezone,
                },
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response, expected=expected)

    @property
    def arrangement_headers(self):
        return {
            **self.headers,
            "Authorization": f"Bearer {settings.CAREERHUB_ARRANGEMENT_API_TOKEN}",
        }

    def confirm_arrangement_dates(
        self, *, case_id, person_public_id, start_date, end_date
    ):
        return self._arrangement_command(
            path=f"arrangements/{case_id}/confirm-dates/",
            payload={
                "person_public_id": str(person_public_id),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

    def confirm_acceptance_receipt(self, *, case_id, letter_id, person_public_id):
        return self._arrangement_command(
            path=f"arrangements/{case_id}/acceptance/{letter_id}/receipt/",
            payload={"person_public_id": str(person_public_id)},
        )

    def _arrangement_command(self, *, path, payload):
        try:
            response = self.session.post(
                f"{self.base_url}/{path}",
                json=payload,
                headers=self.arrangement_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response)

    def download_acceptance(self, *, case_id, letter_id, person_public_id):
        try:
            response = self.session.get(
                f"{self.base_url}/arrangements/{case_id}/acceptance/"
                f"{letter_id}/download/",
                params={"person_public_id": str(person_public_id)},
                headers=self.arrangement_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        if response.status_code != 200:
            raise ApplicationServiceError
        content = response.content
        if (
            response.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/pdf"
            or len(content) > 10 * 1024 * 1024
            or not content.startswith(b"%PDF-")
        ):
            raise ApplicationServiceError
        return AcceptanceDocument(content=content)

    def upload_erasmus_document(
        self, *, case_id, document_id, person_public_id, document
    ):
        try:
            response = self.session.post(
                f"{self.base_url}/erasmus-cases/{case_id}/documents/"
                f"{document_id}/upload/",
                data={"person_public_id": str(person_public_id)},
                files={
                    "document": (
                        Path(document.name).name,
                        document.read(),
                        getattr(document, "content_type", "application/octet-stream"),
                    )
                },
                headers=self.arrangement_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        return self._payload(response)

    def download_erasmus_document(
        self, *, case_id, document_id, version_id, person_public_id
    ):
        try:
            response = self.session.get(
                f"{self.base_url}/erasmus-cases/{case_id}/documents/"
                f"{document_id}/versions/{version_id}/download/",
                params={"person_public_id": str(person_public_id)},
                headers=self.arrangement_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApplicationServiceError from exc
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        content = response.content
        if (
            response.status_code != 200
            or content_type
            not in {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            or len(content) > 10 * 1024 * 1024
        ):
            raise ApplicationServiceError
        return AcceptanceDocument(content=content), content_type

    def record_accommodation_response(
        self, *, case_id, person_public_id, needs_assistance
    ):
        return self._arrangement_command(
            path=f"erasmus-cases/{case_id}/accommodation/",
            payload={
                "person_public_id": str(person_public_id),
                "needs_assistance": needs_assistance,
            },
        )


def get_application_client():
    return PeopleOpsApplicationClient()
