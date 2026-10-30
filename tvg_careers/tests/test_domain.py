from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from horilla.testkit import make_company, make_employee, make_user
from tvg_careers.models import Application, JobOpening
from tvg_careers.services import link_candidate_application, set_job_opening_state
from tvg_careers.tests.factories import make_application, make_job_opening
from tvg_core.tests.factories import make_person, make_venture


class JobOpeningDomainTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic THEVIPGROUP Employer")
        self.venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )

    def test_opening_reuses_horilla_recruitment_structure(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Manufacturing Intern",
        )

        self.assertEqual(opening.title, opening.recruitment.title)
        self.assertEqual(opening.role_description, opening.recruitment.description)
        self.assertEqual(opening.department, opening.job_position.department_id)
        self.assertEqual(opening.number_of_positions, opening.recruitment.vacancy)
        self.assertEqual(opening.publication_state, JobOpening.PublicationState.DRAFT)
        self.assertEqual(opening.venture.legal_employer, opening.recruitment.company_id)

    def test_opening_slug_is_globally_unique(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            slug="synthetic-unique-public-slug",
        )
        other_venture = make_venture(
            company=self.company,
            name="FindMyStay",
            slug="findmystay",
        )

        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                make_job_opening(
                    company=self.company,
                    venture=other_venture,
                    slug=opening.public_slug,
                )

    def test_opening_rejects_brand_from_another_employer(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        other_company = make_company("Synthetic Other Legal Employer", hq=False)
        opening.venture = make_venture(
            company=other_company,
            name="Other Brand",
            slug="other-brand",
        )

        with self.assertRaises(ValidationError):
            opening.save()

    def test_opening_validates_start_window_and_duration(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        opening.expected_start_date_to = opening.expected_start_date_from.replace(
            year=opening.expected_start_date_from.year - 1
        )
        opening.preferred_duration_weeks = opening.minimum_duration_weeks - 1

        with self.assertRaises(ValidationError) as raised:
            opening.save()

        self.assertIn("expected_start_date_to", raised.exception.message_dict)
        self.assertIn("preferred_duration_weeks", raised.exception.message_dict)

    def test_publishing_requires_explicit_permission(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        actor = make_user("synthetic_publisher")
        make_employee(
            company=self.company,
            email="publisher@example.test",
            user=actor,
        )

        with self.assertRaises(PermissionDenied):
            set_job_opening_state(
                actor=actor,
                opening=opening,
                state=JobOpening.PublicationState.PUBLISHED,
            )

        permission = Permission.objects.get(
            content_type__app_label="tvg_careers",
            codename="publish_jobopening",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)
        opening = set_job_opening_state(
            actor=actor,
            opening=opening,
            state=JobOpening.PublicationState.PUBLISHED,
        )

        self.assertEqual(
            opening.publication_state, JobOpening.PublicationState.PUBLISHED
        )
        self.assertTrue(opening.recruitment.is_published)
        self.assertFalse(opening.recruitment.closed)

    def test_publisher_permission_does_not_cross_legal_employers(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        other_company = make_company("Synthetic Publisher Employer", hq=False)
        actor = make_user("synthetic_cross_company_publisher")
        make_employee(
            company=other_company,
            email="cross-company-publisher@example.test",
            user=actor,
        )
        permission = Permission.objects.get(
            content_type__app_label="tvg_careers",
            codename="publish_jobopening",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)

        with self.assertRaises(PermissionDenied):
            set_job_opening_state(
                actor=actor,
                opening=opening,
                state=JobOpening.PublicationState.PUBLISHED,
            )

    def test_closing_requires_explicit_permission_and_reuses_horilla_state(self):
        opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            is_published=True,
        )
        actor = make_user("synthetic_closer")
        make_employee(
            company=self.company,
            email="closer@example.test",
            user=actor,
        )

        with self.assertRaises(PermissionDenied):
            set_job_opening_state(
                actor=actor,
                opening=opening,
                state=JobOpening.PublicationState.CLOSED,
            )

        permission = Permission.objects.get(
            content_type__app_label="tvg_careers",
            codename="close_jobopening",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)
        opening = set_job_opening_state(
            actor=actor,
            opening=opening,
            state=JobOpening.PublicationState.CLOSED,
        )

        self.assertEqual(opening.publication_state, JobOpening.PublicationState.CLOSED)
        self.assertTrue(opening.recruitment.closed)
        self.assertFalse(opening.recruitment.is_published)


class ApplicationIdentityTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Application Employer")
        self.venture = make_venture(
            company=self.company,
            name="FindMyStay",
            slug="findmystay",
        )

    def test_one_person_can_have_multiple_independent_horilla_candidates(self):
        person = make_person(
            company=self.company,
            email="multi-application@example.test",
        )
        first_opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Marketplace Intern",
        )
        second_opening = make_job_opening(
            company=self.company,
            venture=self.venture,
            title="Synthetic Operations Intern",
        )

        first = make_application(opening=first_opening, person=person)
        second = make_application(opening=second_opening, person=person)

        self.assertNotEqual(first.candidate_id, second.candidate_id)
        self.assertNotEqual(first.recruitment.pk, second.recruitment.pk)
        self.assertEqual(person.applications.count(), 2)
        self.assertEqual(
            set(person.applications.values_list("candidate_id", flat=True)),
            {first.candidate_id, second.candidate_id},
        )

    def test_application_link_does_not_change_candidate_pipeline_or_history(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        application = make_application(opening=opening)
        candidate = application.candidate
        original_stage_id = candidate.stage_id_id
        original_history_count = candidate.history.count()

        application.save()
        candidate.refresh_from_db()

        self.assertEqual(candidate.stage_id_id, original_stage_id)
        self.assertEqual(candidate.history.count(), original_history_count)

    def test_application_rejects_candidate_from_different_recruitment(self):
        first_opening = make_job_opening(company=self.company, venture=self.venture)
        second_opening = make_job_opening(company=self.company, venture=self.venture)
        application = make_application(opening=first_opening)
        application.opening = second_opening

        with self.assertRaises(ValidationError):
            application.save()

    def test_link_service_requires_explicit_permission(self):
        opening = make_job_opening(company=self.company, venture=self.venture)
        seeded_application = make_application(opening=opening)
        candidate = seeded_application.candidate
        seeded_application.delete()
        person = make_person(
            company=self.company,
            email="permission-link@example.test",
        )
        actor = make_user("synthetic_application_linker")
        make_employee(
            company=self.company,
            email="application-linker@example.test",
            user=actor,
        )

        with self.assertRaises(PermissionDenied):
            link_candidate_application(
                actor=actor,
                person=person,
                candidate=candidate,
                opening=opening,
            )

        permission = Permission.objects.get(
            content_type__app_label="tvg_careers",
            codename="link_candidate_application",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)
        linked = link_candidate_application(
            actor=actor,
            person=person,
            candidate=candidate,
            opening=opening,
        )
        self.assertIsInstance(linked, Application)
        self.assertEqual(linked.candidate, candidate)
