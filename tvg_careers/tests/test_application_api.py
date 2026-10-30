import json
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from horilla.testkit import make_company
from recruitment.models import (
    Candidate,
    RecruitmentSurvey,
    RecruitmentSurveyAnswer,
    Stage,
    SurveyTemplate,
)
from tvg_careers.models import Application, ApplicationConfirmationEvent
from tvg_careers.questionnaires import ensure_questionnaire_version
from tvg_careers.tests.factories import make_job_opening
from tvg_core.tests.factories import make_person, make_venture

TOKEN = "synthetic-application-command-token-000000000"
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


class CareerHubApplicationAPITests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.company = make_company("THEVIPGROUP Application Employer")
        self.venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )
        self.person = make_person(
            company=self.company,
            email="multi.application@example.test",
            preferred_name="Synthetic Multi Applicant",
        )
        self.client = Client()

    def _settings(self, **overrides):
        values = {
            "ROOT_URLCONF": "tvg_careers.peopleops_urls",
            "CAREERHUB_APPLICATION_API_ENABLED": True,
            "CAREERHUB_APPLICATION_API_TOKEN": TOKEN,
            "TVG_LEGAL_EMPLOYER_ID": self.company.pk,
            "MEDIA_ROOT": self.media.name,
        }
        values.update(overrides)
        return self.settings(**values)

    def _add_question(
        self,
        opening,
        *,
        label="Describe a synthetic project",
        question_type="textarea",
        required=True,
        options="",
    ):
        template = SurveyTemplate._base_manager.create(
            title=f"Synthetic questionnaire {uuid4().hex[:8]}",
            company_id=self.company,
        )
        question = RecruitmentSurvey._base_manager.create(
            question=label,
            type=question_type,
            is_mandatory=required,
            options=options,
            sequence=1,
        )
        question.template_id.add(template)
        opening.recruitment.survey_templates.add(template)
        return question

    def _post(
        self,
        opening,
        *,
        application_id=None,
        token=TOKEN,
        questionnaire=None,
        questionnaire_version=None,
        source="company_website",
        utm_parameters=None,
        cv_content=PDF,
        cv_name="synthetic-cv.pdf",
        cv_content_type="application/pdf",
    ):
        version = questionnaire_version or ensure_questionnaire_version(
            opening.recruitment
        )
        payload = {
            "application_id": str(application_id or uuid4()),
            "person_public_id": str(self.person.public_id),
            "opening_public_id": str(opening.public_id),
            "email": self.person.primary_email,
            "profile": {
                "name": "Synthetic Multi Applicant",
                "phone_number": "+48 123 456 789",
                "country_of_residence": "Poland",
                "university": "Synthetic University",
                "portfolio_url": "https://portfolio.example.test",
            },
            "questionnaire_version": {
                "id": str(version.public_id),
                "number": version.version_number,
                "fingerprint": version.fingerprint,
            },
            "questionnaire": questionnaire or [],
            "source": source,
            "utm_parameters": utm_parameters or {},
        }
        return self.client.post(
            "/internal/careerhub/v1/applications/",
            {
                "payload": json.dumps(payload),
                "cv": SimpleUploadedFile(
                    cv_name, cv_content, content_type=cv_content_type
                ),
            },
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_exact_token_required_and_invalid_pdf_does_not_create_candidate(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        with self._settings():
            missing = self.client.post("/internal/careerhub/v1/applications/")
            wrong = self._post(opening, token="wrong-token")
            invalid = self._post(opening, cv_content=b"not-a-pdf")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(Candidate.objects.count(), 0)
        self.assertFalse(Application.all_objects.exists())

    def test_one_person_applies_to_two_vacancies_with_independent_horilla_records(self):
        first = make_job_opening(
            company=self.company,
            venture=self.venture,
            is_published=True,
            title="Synthetic First Vacancy",
        )
        second = make_job_opening(
            company=self.company,
            venture=self.venture,
            is_published=True,
            title="Synthetic Second Vacancy",
        )
        first_id, second_id = uuid4(), uuid4()
        with self._settings():
            first_response = self._post(first, application_id=first_id)
            second_response = self._post(second, application_id=second_id)

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        applications = Application.all_objects.filter(person=self.person)
        self.assertEqual(applications.count(), 2)
        self.assertEqual(applications.values("candidate_id").distinct().count(), 2)
        self.assertEqual(
            set(applications.values_list("opening_id", flat=True)),
            {first.pk, second.pk},
        )
        self.assertEqual(
            Candidate.objects.filter(email=self.person.primary_email).count(), 2
        )

        assessment_stage = Stage.objects.create(
            recruitment_id=first.recruitment,
            stage="Synthetic Assessment",
            stage_type="test",
            sequence=20,
        )
        first_application = applications.get(opening=first)
        first_application.candidate.stage_id = assessment_stage
        first_application.candidate.save()
        second_application = applications.get(opening=second)
        self.assertEqual(second_application.candidate.stage_id.stage_type, "applied")

    def test_submission_is_idempotent_and_duplicate_opening_is_rejected(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        application_id = uuid4()
        with self._settings():
            created = self._post(opening, application_id=application_id)
            repeated = self._post(opening, application_id=application_id)
            duplicate = self._post(opening, application_id=uuid4())
        self.assertEqual(created.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(Application.all_objects.count(), 1)
        self.assertEqual(Candidate.objects.count(), 1)
        self.assertEqual(ApplicationConfirmationEvent.all_objects.count(), 1)

    def test_withdrawal_is_idempotent_owner_scoped_and_preserves_horilla_history(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        application_id = uuid4()
        with self._settings():
            self._post(opening, application_id=application_id)
            url = f"/internal/careerhub/v1/applications/{application_id}/withdraw/"
            body = json.dumps({"person_public_id": str(self.person.public_id)})
            first = self.client.post(
                url,
                body,
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
            repeated = self.client.post(
                url,
                body,
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
            denied = self.client.post(
                url,
                json.dumps({"person_public_id": str(uuid4())}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(denied.status_code, 404)
        application = Application.all_objects.get(public_id=application_id)
        self.assertIsNotNone(application.withdrawn_at)
        self.assertTrue(application.candidate.canceled)
        self.assertEqual(application.candidate.stage_id.stage_type, "cancelled")
        self.assertGreaterEqual(application.candidate.history.count(), 2)

    def test_status_projection_excludes_internal_comments_ratings_and_other_people(
        self,
    ):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        with self._settings():
            created = self._post(opening)
            status = self.client.get(
                "/internal/careerhub/v1/applications/status/",
                {"person_public_id": str(self.person.public_id)},
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(status.status_code, 200)
        payload = status.json()
        self.assertEqual(len(payload["results"]), 1)
        serialized = json.dumps(payload).lower()
        for forbidden in ("description", "interviewer", "rating", "comment", "email"):
            self.assertNotIn(forbidden, serialized)

    def test_docx_cv_source_utm_and_confirmation_event_are_persisted(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        with self._settings():
            response = self._post(
                opening,
                source="linkedin",
                utm_parameters={
                    "utm_source": "linkedin",
                    "utm_campaign": "synthetic-autumn-internships",
                    "untrusted": "discard-me",
                },
                cv_name="synthetic-cv.docx",
                cv_content=synthetic_docx(),
                cv_content_type=DOCX_CONTENT_TYPE,
            )

        self.assertEqual(response.status_code, 201, response.content)
        application = Application.all_objects.get()
        self.assertEqual(application.source, "linkedin")
        self.assertEqual(
            application.utm_parameters,
            {
                "utm_source": "linkedin",
                "utm_campaign": "synthetic-autumn-internships",
            },
        )
        self.assertEqual(application.candidate.referral_source, "social_media")
        self.assertEqual(application.candidate.referral_source_other, "LinkedIn")
        event = ApplicationConfirmationEvent.all_objects.get(application=application)
        self.assertEqual(event.event_type, "career.application_submitted.v1")
        self.assertEqual(response.json()["confirmation_event_id"], str(event.event_id))
        self.assertNotIn("email", json.dumps(event.payload).lower())

    def test_active_content_docx_is_rejected_before_application_creation(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        with self._settings():
            response = self._post(
                opening,
                cv_name="unsafe-cv.docx",
                cv_content=synthetic_docx(active_content=True),
                cv_content_type=DOCX_CONTENT_TYPE,
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Application.all_objects.exists())
        self.assertFalse(Candidate.objects.exists())
        self.assertFalse(RecruitmentSurveyAnswer._base_manager.exists())

    def test_cross_company_opening_is_not_an_application_target(self):
        other_company = make_company("Synthetic Foreign Application Employer", hq=False)
        other_venture = make_venture(
            company=other_company,
            name="Synthetic Foreign Venture",
            slug="synthetic-foreign-venture",
        )
        opening = make_job_opening(
            company=other_company,
            venture=other_venture,
            is_published=True,
        )

        with self._settings():
            response = self._post(opening)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Application.all_objects.exists())
        self.assertFalse(Candidate.objects.exists())

    def test_required_answers_and_current_version_are_enforced_before_creation(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        stale_version = ensure_questionnaire_version(opening.recruitment)
        question = self._add_question(opening)
        current_version = ensure_questionnaire_version(opening.recruitment)
        public_question_id = current_version.definition_snapshot[0]["public_id"]

        with self._settings():
            stale = self._post(
                opening,
                questionnaire_version=stale_version,
                questionnaire=[
                    {"id": public_question_id, "answer": "Synthetic answer"}
                ],
            )
            missing = self._post(
                opening,
                questionnaire_version=current_version,
                questionnaire=[],
            )
            created = self._post(
                opening,
                questionnaire_version=current_version,
                questionnaire=[
                    {"id": public_question_id, "answer": "Synthetic answer"}
                ],
            )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(Candidate.objects.count(), 1)
        application = Application.all_objects.get()
        self.assertEqual(application.questionnaire_version, current_version)
        self.assertEqual(
            application.questionnaire_snapshot[0]["question"], question.question
        )
        self.assertEqual(
            application.questionnaire_snapshot[0]["answer"], "Synthetic answer"
        )
        native_answer = RecruitmentSurveyAnswer._base_manager.get()
        self.assertEqual(
            json.loads(native_answer.answer_json),
            {question.question: ["Synthetic answer"]},
        )

    def test_whitespace_required_answer_is_rejected_and_optional_is_snapshotted(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        required = self._add_question(
            opening,
            label="Required synthetic motivation",
            required=True,
        )
        optional = self._add_question(
            opening,
            label="Optional synthetic project",
            required=False,
        )
        version = ensure_questionnaire_version(opening.recruitment)
        question_ids = {
            item["label"]: item["public_id"] for item in version.definition_snapshot
        }

        with self._settings():
            rejected = self._post(
                opening,
                questionnaire_version=version,
                questionnaire=[
                    {"id": question_ids[required.question], "answer": "   "},
                ],
            )
            created = self._post(
                opening,
                questionnaire_version=version,
                questionnaire=[
                    {
                        "id": question_ids[required.question],
                        "answer": "Synthetic motivation",
                    },
                ],
            )

        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(created.status_code, 201, created.content)
        snapshot = {
            item["question"]: item["answer"]
            for item in Application.all_objects.get().questionnaire_snapshot
        }
        self.assertEqual(snapshot[required.question], "Synthetic motivation")
        self.assertIsNone(snapshot[optional.question])

    def test_unrecognized_source_is_rejected(self):
        opening = make_job_opening(
            company=self.company, venture=self.venture, is_published=True
        )
        with self._settings():
            response = self._post(opening, source="untrusted-source")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Application.all_objects.exists())
