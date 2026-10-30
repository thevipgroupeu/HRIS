from importlib import import_module
from uuid import uuid4

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase

from horilla.testkit import make_company
from recruitment.models import RecruitmentSurvey, SurveyTemplate
from tvg_careers.accounts.forms import CandidateApplicationForm
from tvg_careers.forms import JobOpeningAdminForm
from tvg_careers.public_catalog import get_public_opening
from tvg_careers.questionnaires import (
    QuestionnaireConfigurationError,
    current_questionnaire_version,
    ensure_questionnaire_version,
)
from tvg_careers.tests.factories import make_job_opening
from tvg_core.tests.factories import make_venture


class RecruitmentQuestionnaireTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Questionnaire Employer")
        self.fluxops = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )

    def _template(self, *, company=None, title=None):
        return SurveyTemplate._base_manager.create(
            title=title or f"Synthetic template {uuid4().hex[:8]}",
            company_id=company or self.company,
        )

    def _question(
        self,
        template,
        *,
        label,
        question_type="text",
        required=False,
        options="",
        sequence=1,
    ):
        question = RecruitmentSurvey._base_manager.create(
            question=label,
            type=question_type,
            is_mandatory=required,
            options=options,
            sequence=sequence,
        )
        question.template_id.add(template)
        return question

    def test_native_templates_publish_required_optional_common_field_types(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            is_published=True,
        )
        template = self._template()
        self._question(
            template,
            label="Hybrid work availability",
            question_type="checkbox",
            required=True,
            sequence=1,
        )
        self._question(
            template,
            label="Motivation",
            question_type="textarea",
            required=True,
            sequence=2,
        )
        self._question(
            template,
            label="Relevant skill areas",
            question_type="multiple",
            options="Python, Django, Product research",
            sequence=3,
        )
        opening.recruitment.survey_templates.add(template)
        version = ensure_questionnaire_version(opening.recruitment)

        with self.settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            payload = get_public_opening(
                venture_slug="fluxops",
                opening_slug=opening.public_slug,
            )

        self.assertTrue(payload["accepting_applications"])
        self.assertEqual(payload["questionnaire_version"]["id"], str(version.public_id))
        self.assertEqual(payload["questionnaire_version"]["number"], 2)
        self.assertEqual(
            [question["type"] for question in payload["questionnaire"]],
            ["checkbox", "textarea", "multiple"],
        )
        self.assertEqual(
            [question["required"] for question in payload["questionnaire"]],
            [True, True, False],
        )
        self.assertNotIn("native_question_id", payload["questionnaire"][0])

    def test_venture_recruitments_can_use_different_questionnaires(self):
        findmystay = make_venture(
            company=self.company,
            name="FindMyStay",
            slug="findmystay",
        )
        fluxops_opening = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            is_published=True,
        )
        stay_opening = make_job_opening(
            company=self.company,
            venture=findmystay,
            is_published=True,
        )
        fluxops_template = self._template(title="Synthetic FluxOps questions")
        stay_template = self._template(title="Synthetic FindMyStay questions")
        self._question(
            fluxops_template,
            label="Describe a manufacturing software project",
            required=True,
        )
        self._question(
            stay_template,
            label="Describe a marketplace research project",
            required=True,
        )
        fluxops_opening.recruitment.survey_templates.add(fluxops_template)
        stay_opening.recruitment.survey_templates.add(stay_template)
        ensure_questionnaire_version(fluxops_opening.recruitment)
        ensure_questionnaire_version(stay_opening.recruitment)

        with self.settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            fluxops_payload = get_public_opening(
                venture_slug="fluxops",
                opening_slug=fluxops_opening.public_slug,
            )
            stay_payload = get_public_opening(
                venture_slug="findmystay",
                opening_slug=stay_opening.public_slug,
            )

        self.assertEqual(
            fluxops_payload["questionnaire"][0]["label"],
            "Describe a manufacturing software project",
        )
        self.assertEqual(
            stay_payload["questionnaire"][0]["label"],
            "Describe a marketplace research project",
        )
        self.assertNotEqual(
            fluxops_payload["questionnaire_version"]["id"],
            stay_payload["questionnaire_version"]["id"],
        )

    def test_changed_native_question_creates_new_immutable_version(self):
        opening = make_job_opening(company=self.company, venture=self.fluxops)
        template = self._template()
        question = self._question(
            template,
            label="Original synthetic motivation question",
            required=True,
        )
        opening.recruitment.survey_templates.add(template)
        first = ensure_questionnaire_version(opening.recruitment)

        question.question = "Revised synthetic motivation question"
        question.save()
        second = ensure_questionnaire_version(opening.recruitment)

        self.assertEqual(second.version_number, first.version_number + 1)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            first.definition_snapshot[0]["public_id"],
            second.definition_snapshot[0]["public_id"],
        )
        first.definition_snapshot = []
        with self.assertRaises(ValidationError):
            first.save()

    def test_phase_six_data_migration_backfills_native_questionnaire(self):
        opening = make_job_opening(company=self.company, venture=self.fluxops)
        template = self._template()
        question = self._question(
            template,
            label="Synthetic backfilled motivation",
            required=True,
        )
        opening.recruitment.survey_templates.add(template)
        opening.recruitment.tvg_questionnaire_versions.all().delete()

        migration = import_module(
            "tvg_careers.migrations.0004_application_questionnaire_snapshot_and_more"
        )
        migration.backfill_questionnaire_versions(apps, None)

        version = opening.recruitment.tvg_questionnaire_versions.get()
        self.assertEqual(version.version_number, 1)
        self.assertEqual(version.definition_snapshot[0]["label"], question.question)
        self.assertEqual(
            current_questionnaire_version(opening.recruitment).pk,
            version.pk,
        )

    def test_cross_company_template_is_not_available_or_publishable(self):
        opening = make_job_opening(company=self.company, venture=self.fluxops)
        other_company = make_company("Synthetic Other Questionnaire Employer", hq=False)
        other_template = self._template(
            company=other_company,
            title="Synthetic other-company questionnaire",
        )
        self._question(
            other_template,
            label="Other company confidential question",
            required=True,
        )

        form = JobOpeningAdminForm(instance=opening)
        self.assertNotIn(other_template, form.fields["survey_templates"].queryset)

        opening.recruitment.survey_templates.add(other_template)
        with self.assertRaises(QuestionnaireConfigurationError):
            ensure_questionnaire_version(opening.recruitment)

    def test_question_shared_with_another_company_fails_closed(self):
        opening = make_job_opening(company=self.company, venture=self.fluxops)
        local_template = self._template(title="Synthetic local questionnaire")
        other_company = make_company("Synthetic Shared Question Employer", hq=False)
        foreign_template = self._template(
            company=other_company,
            title="Synthetic foreign shared questionnaire",
        )
        question = self._question(
            local_template,
            label="Synthetic shared mutable question",
            required=True,
        )
        question.template_id.add(foreign_template)
        opening.recruitment.survey_templates.add(local_template)

        with self.assertRaises(QuestionnaireConfigurationError):
            ensure_questionnaire_version(opening.recruitment)

    def test_generic_file_questions_are_blocked_to_avoid_identity_documents(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.fluxops,
            is_published=True,
        )
        template = self._template()
        self._question(
            template,
            label="Upload an identity document",
            question_type="file",
            required=True,
        )
        opening.recruitment.survey_templates.add(template)

        with self.assertRaises(QuestionnaireConfigurationError):
            ensure_questionnaire_version(opening.recruitment)
        with self.settings(TVG_LEGAL_EMPLOYER_ID=self.company.pk):
            payload = get_public_opening(
                venture_slug="fluxops",
                opening_slug=opening.public_slug,
            )
        self.assertFalse(payload["questionnaire_supported"])
        self.assertFalse(payload["accepting_applications"])
        self.assertEqual(payload["questionnaire"], [])

    def test_candidate_form_validates_required_and_choice_answers_server_side(self):
        job = {
            "questionnaire": [
                {
                    "id": "availability",
                    "label": "Hybrid work availability",
                    "type": "checkbox",
                    "required": True,
                    "options": [],
                },
                {
                    "id": "skills",
                    "label": "Relevant skills",
                    "type": "multiple",
                    "required": True,
                    "options": ["Python", "Django"],
                },
                {
                    "id": "motivation",
                    "label": "Motivation",
                    "type": "textarea",
                    "required": True,
                    "options": [],
                },
            ]
        }
        invalid = CandidateApplicationForm(
            {
                "question_availability": "false",
                "question_skills": ["Unknown"],
                "question_motivation": "",
                "confirmation": "on",
            },
            job=job,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("question_skills", invalid.errors)
        self.assertIn("question_motivation", invalid.errors)

        valid = CandidateApplicationForm(
            {
                "question_availability": "false",
                "question_skills": ["Python", "Django"],
                "question_motivation": "A synthetic motivation statement.",
                "confirmation": "on",
            },
            job=job,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        snapshot = valid.questionnaire_snapshot()
        self.assertIs(snapshot[0]["answer"], False)
        self.assertEqual(snapshot[1]["answer"], ["Python", "Django"])
