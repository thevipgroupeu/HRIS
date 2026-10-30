from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tvg_careers.accounts.application_client import (
    AcceptanceDocument,
    ApplicationServiceError,
)
from tvg_careers.accounts.models import (
    CandidateApplication,
    CandidateProfile,
    CandidateProfileDocument,
    CandidateSecurityEvent,
)
from tvg_careers.accounts.portal_services import submit_application
from tvg_careers.accounts.tests.factories import make_candidate_account

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def synthetic_docx(*, active_content=False):
    content = BytesIO()
    with ZipFile(content, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
        if active_content:
            archive.writestr("word/vbaProject.bin", b"synthetic-macro")
    return content.getvalue()


def job(*, public_id=None, slug="synthetic-role", venture="fluxops"):
    return {
        "public_id": str(public_id or uuid4()),
        "slug": slug,
        "title": f"Synthetic {venture} role",
        "venture": {"slug": venture, "name": venture.title()},
        "accepting_applications": True,
        "url_path": f"/opportunities/{venture}/{slug}/",
        "questionnaire_version": {
            "id": str(uuid4()),
            "number": 1,
            "fingerprint": "a" * 64,
        },
        "questionnaire": [
            {
                "id": "17",
                "label": "Why this synthetic role?",
                "type": "textarea",
                "required": True,
                "options": [],
            }
        ],
    }


class FakeJobs:
    def __init__(self, value):
        self.value = value

    def get_job(self, venture_slug, opening_slug):
        return deepcopy(self.value)


class FakeApplications:
    def submit(self, **kwargs):
        return {
            "status": {"code": "under_review", "label": "Under review"},
        }

    def withdraw(self, **kwargs):
        return {"status": {"code": "withdrawn", "label": "Withdrawn"}}


class FailingApplications:
    def submit(self, **kwargs):
        raise ApplicationServiceError("Synthetic service outage")


class RecordingApplications:
    def __init__(self):
        self.submission = None

    def submit(self, **kwargs):
        self.submission = kwargs
        return {"status": {"code": "under_review", "label": "Under review"}}


class VerificationApplications:
    def __init__(self):
        self.upload = None

    def upload_verification(self, **kwargs):
        self.upload = kwargs
        return {"evidence_id": str(uuid4()), "scan_status": "pending"}


class InterviewApplications:
    def __init__(self):
        self.booked = None
        self.rescheduled = None

    def book_interview(self, **kwargs):
        self.booked = kwargs
        return {"booking_id": str(uuid4())}

    def reschedule_interview(self, **kwargs):
        self.rescheduled = kwargs
        return {"booking_id": str(uuid4())}


class ArrangementApplications:
    def __init__(self):
        self.confirmed_dates = None
        self.receipt = None
        self.download = None

    def confirm_arrangement_dates(self, **kwargs):
        self.confirmed_dates = kwargs
        return {"arrangement": {"arrangement_status": "candidate_confirmed"}}

    def confirm_acceptance_receipt(self, **kwargs):
        self.receipt = kwargs
        return {"arrangement": {"acceptance_status": "received"}}

    def download_acceptance(self, **kwargs):
        self.download = kwargs
        return AcceptanceDocument(content=PDF)


def verification_status(application, requirement_id):
    return {
        str(application.pk): {
            "application_id": str(application.pk),
            "status": {"code": "under_review", "label": "Under review"},
            "interviews": [],
            "offer": None,
            "onboarding": {"status": "Not started"},
            "verification": {
                "status": "revision_needed",
                "label": "Revision needed",
                "interview_eligible": False,
                "next_action": "Upload the corrected documents shown below.",
                "due_at": None,
                "requirements": [
                    {
                        "id": str(requirement_id),
                        "category": "student_status",
                        "title": "Synthetic student confirmation",
                        "instructions": "Upload a synthetic confirmation.",
                        "required": True,
                        "status": "revision_needed",
                        "status_label": "Revision needed",
                        "allowed_formats": ["pdf"],
                        "max_size_mb": 1,
                        "revision_reason": "Upload a clearer synthetic page.",
                        "accepting_upload": True,
                    }
                ],
                "notifications": [
                    {
                        "id": str(uuid4()),
                        "message": "A corrected document is required.",
                        "next_action": "Upload a corrected document.",
                        "occurred_at": "2026-10-26T10:00:00+00:00",
                    }
                ],
            },
        }
    }


def arrangement_status(application, case_id, letter_id):
    return {
        str(application.pk): {
            "application_id": str(application.pk),
            "status": {"code": "selected", "label": "Selected"},
            "interviews": [],
            "offer": None,
            "onboarding": {"status": "Not started"},
            "verification": {
                "status": "verified",
                "next_action": "Verification complete.",
                "notifications": [],
                "requirements": [],
            },
            "arrangement": {
                "id": str(case_id),
                "arrangement_status": "candidate_confirmed",
                "arrangement_status_label": "Candidate confirmed",
                "acceptance_status": "sent",
                "acceptance_status_label": "Sent",
                "university": "Synthetic University",
                "programme": "Synthetic Programme",
                "proposed_start_date": "2027-01-15",
                "proposed_end_date": "2027-04-15",
                "confirmed_start_date": "2027-01-15",
                "confirmed_end_date": "2027-04-15",
                "internship_duration_days": 91,
                "working_model": "Hybrid",
                "work_location": "Synthetic Warsaw Office",
                "outstanding_requirements": [],
                "can_confirm_dates": True,
                "letters": [
                    {
                        "id": str(letter_id),
                        "letter_number": "SYN-ACC/2026/00001",
                        "version": 1,
                        "issue_date": "2026-10-26",
                        "status": "sent",
                        "status_label": "Sent",
                        "downloadable": True,
                        "can_confirm_receipt": True,
                    }
                ],
                "notifications": [],
                "learning_agreement_ready": False,
            },
        }
    }


class CandidatePortalTests(TestCase):
    def setUp(self):
        self.private_media = TemporaryDirectory()
        self.storage = override_settings(
            DEBUG_PROPAGATE_EXCEPTIONS=True,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": self.private_media.name},
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
                },
            },
        )
        self.storage.enable()
        self.account = make_candidate_account()
        self.other = make_candidate_account(email="other.portal@example.test")
        self.client.force_login(self.account)

    def tearDown(self):
        self.storage.disable()
        self.private_media.cleanup()

    def _upload(self, kind="cv", name="synthetic-cv.pdf", content=PDF):
        content_type = (
            DOCX_CONTENT_TYPE if name.endswith(".docx") else "application/pdf"
        )
        return self.client.post(
            reverse("careerhub:candidate-documents"),
            {
                "kind": kind,
                "document": SimpleUploadedFile(
                    name, content, content_type=content_type
                ),
            },
        )

    def test_profile_has_reusable_study_contact_dates_languages_and_erasmus_data(self):
        response = self.client.post(
            reverse("careerhub:candidate-profile"),
            {
                "first_name": "Ada",
                "last_name": "Synthetic",
                "phone_number": "+48 123 456 789",
                "country_of_residence": "Poland",
                "university": "Synthetic University",
                "degree_programme": "MSc Software Systems",
                "field_of_study": "Computer Science",
                "expected_graduation": "2027-06-30",
                "languages": "Polish, English, English",
                "linkedin_url": "https://www.linkedin.com/in/synthetic",
                "github_url": "https://github.com/synthetic",
                "portfolio_url": "https://portfolio.example.test",
                "preferred_start_date": "2027-02-01",
                "preferred_end_date": "2027-07-31",
                "preferred_duration_weeks": "24",
                "erasmus_declaration": "eligible_planning",
                "talent_pool_consent": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = CandidateProfile.objects.get(account=self.account)
        self.assertEqual(profile.languages, ["Polish", "English"])
        self.assertEqual(profile.university, "Synthetic University")
        self.assertEqual(profile.erasmus_declaration, "eligible_planning")

    def test_documents_are_private_versioned_and_owner_authorized(self):
        created = self._upload()
        self.assertEqual(created.status_code, 302)
        first = CandidateProfileDocument.objects.get(account=self.account)

        invalid = self.client.post(
            reverse("careerhub:candidate-documents"),
            {
                "kind": "cv",
                "document": SimpleUploadedFile("not.pdf", b"not a pdf"),
            },
        )
        self.assertEqual(invalid.status_code, 400)

        self._upload(name="replacement.pdf")
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        current = CandidateProfileDocument.objects.get(
            account=self.account, is_current=True
        )
        self.assertNotEqual(first.pk, current.pk)
        own = self.client.get(
            reverse("careerhub:candidate-document-download", args=[current.pk])
        )
        self.assertEqual(own.status_code, 200)
        with patch("django.http.response.signals.request_finished.send"):
            own.close()
        self.client.force_login(self.other)
        denied = self.client.get(
            reverse("careerhub:candidate-document-download", args=[current.pk])
        )
        self.assertEqual(denied.status_code, 404)

    def test_candidate_document_size_limit_is_enforced(self):
        oversized = self._upload(content=PDF + (b"x" * (5 * 1024 * 1024)))

        self.assertContains(oversized, "5 MB", status_code=400)
        self.assertFalse(
            CandidateProfileDocument.objects.filter(account=self.account).exists()
        )

    def test_docx_cv_is_accepted_but_active_content_is_rejected(self):
        accepted = self._upload(
            name="synthetic-cv.docx",
            content=synthetic_docx(),
        )
        self.assertEqual(accepted.status_code, 302)
        document = CandidateProfileDocument.objects.get(account=self.account)
        self.assertEqual(document.content_type, DOCX_CONTENT_TYPE)

        rejected = self._upload(
            name="active-cv.docx",
            content=synthetic_docx(active_content=True),
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            CandidateProfileDocument.objects.filter(account=self.account).count(), 1
        )

    def test_submission_freezes_profile_documents_and_questionnaire(self):
        profile = CandidateProfile.objects.create(
            account=self.account,
            university="Submission University",
            languages=["English"],
        )
        self._upload()
        opening = job()
        url = reverse(
            "careerhub:candidate-application-create",
            args=[opening["venture"]["slug"], opening["slug"]],
        )
        with patch(
            "tvg_careers.accounts.portal_views.get_public_job_client",
            return_value=FakeJobs(opening),
        ), patch(
            "tvg_careers.accounts.portal_services.get_application_client",
            return_value=FakeApplications(),
        ):
            response = self.client.post(
                url,
                {
                    "question_17": "A synthetic answer",
                    "confirmation": "on",
                },
            )

        self.assertEqual(response.status_code, 302, response.content.decode())
        application = CandidateApplication.objects.get(account=self.account)
        self.assertEqual(application.state, CandidateApplication.State.SUBMITTED)
        self.assertEqual(
            application.profile_snapshot["university"], "Submission University"
        )
        self.assertEqual(
            application.questionnaire_snapshot[0]["answer"], "A synthetic answer"
        )
        self.assertTrue(application.cv_snapshot.name)

        profile.university = "Changed Later University"
        profile.save()
        application.refresh_from_db()
        self.assertEqual(
            application.profile_snapshot["university"], "Submission University"
        )
        application.profile_snapshot = {"tampered": True}
        with self.assertRaises(ValidationError):
            application.save()

    def test_failed_delivery_retry_reuses_the_original_frozen_snapshot(self):
        profile = CandidateProfile.objects.create(
            account=self.account,
            university="Original University",
        )
        self._upload(name="original-cv.pdf")
        opening = job()
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=opening["public_id"],
            opening_slug=opening["slug"],
            opening_title=opening["title"],
            venture_slug=opening["venture"]["slug"],
            venture_name=opening["venture"]["name"],
        )
        original_answers = [{"id": "17", "answer": "Original answer"}]

        with patch(
            "tvg_careers.accounts.portal_services.get_application_client",
            return_value=FailingApplications(),
        ), self.assertRaises(ApplicationServiceError):
            submit_application(
                application=application,
                account=self.account,
                profile=profile,
                questionnaire=original_answers,
                questionnaire_version=opening["questionnaire_version"],
            )

        application.refresh_from_db()
        self.assertEqual(application.state, CandidateApplication.State.DRAFT)
        self.assertEqual(
            application.profile_snapshot["university"], "Original University"
        )
        self.assertEqual(application.cv_original_name, "original-cv.pdf")

        profile.university = "Changed University"
        profile.save()
        self._upload(name="replacement-cv.pdf")
        recorder = RecordingApplications()
        with patch(
            "tvg_careers.accounts.portal_services.get_application_client",
            return_value=recorder,
        ):
            submit_application(
                application=application,
                account=self.account,
                profile=profile,
                questionnaire=[{"id": "17", "answer": "Changed answer"}],
                questionnaire_version=opening["questionnaire_version"],
            )

        self.assertEqual(
            recorder.submission["profile"]["university"], "Original University"
        )
        self.assertEqual(recorder.submission["questionnaire"], original_answers)
        self.assertEqual(recorder.submission["cv"].original_name, "original-cv.pdf")

    def test_duplicate_opening_is_not_resubmitted_and_other_user_cannot_withdraw(self):
        opening = job()
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=opening["public_id"],
            opening_slug=opening["slug"],
            opening_title=opening["title"],
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        url = reverse(
            "careerhub:candidate-application-create", args=["fluxops", opening["slug"]]
        )
        with patch(
            "tvg_careers.accounts.portal_views.get_public_job_client",
            return_value=FakeJobs(opening),
        ):
            duplicate = self.client.get(url)
        self.assertRedirects(
            duplicate,
            reverse("careerhub:candidate-dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(CandidateApplication.objects.count(), 1)

        self.client.force_login(self.other)
        denied = self.client.post(
            reverse("careerhub:candidate-application-withdraw", args=[application.pk])
        )
        self.assertEqual(denied.status_code, 404)

        self.client.force_login(self.account)
        with patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=FakeApplications(),
        ):
            withdrawn = self.client.post(
                reverse(
                    "careerhub:candidate-application-withdraw", args=[application.pk]
                )
            )
        self.assertEqual(withdrawn.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, CandidateApplication.State.WITHDRAWN)

    def test_one_account_can_hold_independent_applications_to_two_ventures(self):
        first = job(venture="fluxops", slug="first-role")
        second = job(venture="findmystay", slug="second-role")
        for opening in (first, second):
            CandidateApplication.objects.create(
                account=self.account,
                opening_public_id=opening["public_id"],
                opening_slug=opening["slug"],
                opening_title=opening["title"],
                venture_slug=opening["venture"]["slug"],
                venture_name=opening["venture"]["name"],
            )
        self.assertEqual(
            CandidateApplication.objects.filter(account=self.account).count(), 2
        )

    def test_dashboard_shows_candidate_safe_verification_and_next_action(self):
        requirement_id = uuid4()
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-verification-role",
            opening_title="Synthetic verification role",
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        remote = verification_status(application, requirement_id)
        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ):
            response = self.client.get(reverse("careerhub:candidate-dashboard"))

        self.assertContains(response, "PRE-INTERVIEW VERIFICATION")
        self.assertContains(response, "Synthetic student confirmation")
        self.assertContains(response, "Upload a clearer synthetic page.")
        self.assertContains(response, str(requirement_id))
        self.assertNotContains(response, "internal_note")

    def test_dashboard_displays_candidate_and_company_interview_timezones(self):
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-interview-role",
            opening_title="Synthetic interview role",
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        invitation_id = uuid4()
        slot_id = uuid4()
        remote = {
            str(application.pk): {
                "application_id": str(application.pk),
                "status": {"code": "interview", "label": "Interview stage"},
                "interviews": [
                    {
                        "id": str(invitation_id),
                        "type": "Synthetic structured interview",
                        "location_mode": "video",
                        "status": "invited",
                        "company_timezone": "Europe/Warsaw",
                        "candidate_timezone": "Europe/Paris",
                        "instructions": "Join the synthetic call.",
                        "available_slots": [
                            {
                                "slot_id": str(slot_id),
                                "candidate_display": "03 Nov 2026, 10:00",
                                "candidate_timezone": "Europe/Paris",
                                "company_display": "03 Nov 2026, 10:00",
                                "company_timezone": "Europe/Warsaw",
                            }
                        ],
                        "booking": None,
                        "notifications": [],
                    }
                ],
                "offer": None,
                "onboarding": {"status": "Not started"},
                "verification": {
                    "status": "verified",
                    "next_action": "Verification complete.",
                    "notifications": [],
                    "requirements": [],
                },
            }
        }
        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ):
            response = self.client.get(reverse("careerhub:candidate-dashboard"))
        self.assertContains(response, "Europe/Warsaw")
        self.assertContains(response, "Europe/Paris")
        self.assertContains(response, str(slot_id))
        self.assertNotContains(response, "private_notes")

    def test_interview_booking_and_reschedule_are_owner_scoped_and_audited(self):
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-booking-role",
            opening_title="Synthetic booking role",
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        invitation_id = uuid4()
        slot_id = uuid4()
        booking_url = reverse(
            "careerhub:candidate-interview-book",
            args=(application.pk, invitation_id),
        )
        service = InterviewApplications()
        with patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=service,
        ):
            response = self.client.post(
                booking_url,
                {"slot_id": slot_id, "candidate_timezone": "Europe/Paris"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            service.booked["person_public_id"], self.account.person_public_id
        )
        self.assertEqual(service.booked["slot_id"], slot_id)
        self.assertEqual(self.client.session["candidate_timezone"], "Europe/Paris")
        self.assertTrue(
            CandidateSecurityEvent.objects.filter(
                account=self.account,
                event_type=CandidateSecurityEvent.EventType.INTERVIEW_BOOKED,
            ).exists()
        )

        replacement_id = uuid4()
        reschedule_url = reverse(
            "careerhub:candidate-interview-reschedule",
            args=(application.pk, invitation_id),
        )
        with patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=service,
        ):
            rescheduled = self.client.post(
                reschedule_url,
                {
                    "slot_id": replacement_id,
                    "candidate_timezone": "Europe/Tallinn",
                },
            )
        self.assertEqual(rescheduled.status_code, 302)
        self.assertEqual(service.rescheduled["slot_id"], replacement_id)
        self.assertTrue(
            CandidateSecurityEvent.objects.filter(
                account=self.account,
                event_type=CandidateSecurityEvent.EventType.INTERVIEW_RESCHEDULED,
            ).exists()
        )

        self.client.force_login(self.other)
        with patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=service,
        ):
            denied = self.client.post(
                booking_url,
                {"slot_id": slot_id, "candidate_timezone": "Europe/Paris"},
            )
        self.assertEqual(denied.status_code, 404)

    def test_candidate_confirms_dates_downloads_letter_and_records_receipt(self):
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-acceptance-role",
            opening_title="Synthetic acceptance role",
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        case_id = uuid4()
        letter_id = uuid4()
        remote = arrangement_status(application, case_id, letter_id)
        service = ArrangementApplications()

        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ):
            dashboard = self.client.get(reverse("careerhub:candidate-dashboard"))
        self.assertContains(dashboard, "Selected-candidate arrangements")
        self.assertContains(dashboard, "Confirm these dates")
        self.assertContains(dashboard, "SYN-ACC/2026/00001")

        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ), patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=service,
        ):
            confirmation = self.client.post(
                reverse(
                    "careerhub:candidate-arrangement-confirm-dates",
                    args=(application.pk, case_id),
                ),
                {"start_date": "2027-01-15", "end_date": "2027-04-15"},
            )
            download = self.client.get(
                reverse(
                    "careerhub:candidate-acceptance-download",
                    args=(application.pk, case_id, letter_id),
                )
            )
            receipt = self.client.post(
                reverse(
                    "careerhub:candidate-acceptance-receipt",
                    args=(application.pk, case_id, letter_id),
                )
            )

        self.assertEqual(confirmation.status_code, 302)
        self.assertEqual(
            service.confirmed_dates["person_public_id"],
            self.account.person_public_id,
        )
        self.assertEqual(
            service.confirmed_dates["start_date"].isoformat(), "2027-01-15"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, PDF)
        self.assertEqual(download["Cache-Control"], "private, no-store")
        self.assertEqual(receipt.status_code, 302)
        self.assertEqual(service.receipt["letter_id"], letter_id)
        self.assertEqual(service.download["letter_id"], letter_id)
        self.assertTrue(
            CandidateSecurityEvent.objects.filter(
                account=self.account,
                event_type=CandidateSecurityEvent.EventType.ARRANGEMENT_DATES_CONFIRMED,
            ).exists()
        )
        self.assertTrue(
            CandidateSecurityEvent.objects.filter(
                account=self.account,
                event_type=CandidateSecurityEvent.EventType.ACCEPTANCE_RECEIVED,
            ).exists()
        )

        self.client.force_login(self.other)
        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ):
            denied = self.client.post(
                reverse(
                    "careerhub:candidate-arrangement-confirm-dates",
                    args=(application.pk, case_id),
                ),
                {"start_date": "2027-01-15", "end_date": "2027-04-15"},
            )
        self.assertEqual(denied.status_code, 404)

    def test_verification_upload_is_owner_scoped_validated_and_audited(self):
        requirement_id = uuid4()
        application = CandidateApplication.objects.create(
            account=self.account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-upload-role",
            opening_title="Synthetic upload role",
            venture_slug="fluxops",
            venture_name="FluxOps",
            state=CandidateApplication.State.SUBMITTED,
            submitted_at=self.account.date_joined,
        )
        remote = verification_status(application, requirement_id)
        url = reverse(
            "careerhub:candidate-verification-upload",
            args=(application.pk, requirement_id),
        )
        service = VerificationApplications()
        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ), patch(
            "tvg_careers.accounts.portal_views.get_application_client",
            return_value=service,
        ):
            invalid = self.client.post(
                url,
                {
                    "document": SimpleUploadedFile(
                        "synthetic.txt", b"not a document", content_type="text/plain"
                    )
                },
            )
            accepted = self.client.post(
                url,
                {
                    "document": SimpleUploadedFile(
                        "synthetic-proof.pdf", PDF, content_type="application/pdf"
                    )
                },
            )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(service.upload["application_id"], application.pk)
        self.assertEqual(service.upload["requirement_id"], requirement_id)
        event = CandidateSecurityEvent.objects.get(
            event_type=CandidateSecurityEvent.EventType.VERIFICATION_UPLOADED
        )
        self.assertEqual(event.metadata["application_id"], str(application.pk))
        self.assertNotIn("document", event.metadata)

        self.client.force_login(self.other)
        with patch(
            "tvg_careers.accounts.portal_views.sync_candidate_dashboard",
            return_value=remote,
        ):
            denied = self.client.post(
                url,
                {
                    "document": SimpleUploadedFile(
                        "synthetic-proof.pdf", PDF, content_type="application/pdf"
                    )
                },
            )
        self.assertEqual(denied.status_code, 404)
