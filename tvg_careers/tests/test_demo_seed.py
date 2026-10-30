from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from horilla.testkit import make_company
from tvg_careers.models import JobOpening
from tvg_core.models import Venture


class CareerDemoSeedTests(TestCase):
    def setUp(self):
        self.company = make_company("THEVIPGROUP Demo Sp. z o.o.")

    @override_settings(DEBUG=True)
    def test_seed_creates_one_draft_synthetic_role_per_venture_idempotently(self):
        call_command(
            "seed_tvg_career_demo",
            company_id=self.company.pk,
            confirm_synthetic=True,
            verbosity=0,
        )
        call_command(
            "seed_tvg_career_demo",
            company_id=self.company.pk,
            confirm_synthetic=True,
            verbosity=0,
        )

        ventures = Venture.all_objects.filter(legal_employer=self.company)
        openings = JobOpening.all_objects.filter(
            venture__legal_employer=self.company
        ).select_related("recruitment", "venture")
        self.assertEqual(
            set(ventures.values_list("name", flat=True)),
            {"FluxOps", "FindMyStay", "THEVIPGROUP"},
        )
        self.assertEqual(openings.count(), 3)
        self.assertEqual(
            openings.values("venture_id").distinct().count(),
            3,
        )
        self.assertTrue(
            all(
                opening.publication_state == JobOpening.PublicationState.DRAFT
                for opening in openings
            )
        )
        self.assertTrue(
            all(
                opening.recruitment.title.startswith("[SYNTHETIC")
                for opening in openings
            )
        )

    @override_settings(DEBUG=True)
    def test_seed_requires_explicit_synthetic_confirmation(self):
        with self.assertRaises(CommandError):
            call_command(
                "seed_tvg_career_demo",
                company_id=self.company.pk,
                verbosity=0,
            )

    @override_settings(DEBUG=True, HORILLA_ENV="development")
    def test_seed_can_publish_only_for_explicit_local_review(self):
        call_command(
            "seed_tvg_career_demo",
            company_id=self.company.pk,
            confirm_synthetic=True,
            publish_for_local_review=True,
            verbosity=0,
        )

        openings = JobOpening.all_objects.filter(
            venture__legal_employer=self.company
        ).select_related("recruitment")
        self.assertEqual(openings.count(), 3)
        self.assertTrue(all(opening.is_publicly_available() for opening in openings))
        self.assertEqual(
            set(openings.values_list("opportunity_type", flat=True)),
            {JobOpening.OpportunityType.INTERNSHIP},
        )

    @override_settings(DEBUG=False)
    def test_seed_refuses_production_mode(self):
        with self.assertRaises(CommandError):
            call_command(
                "seed_tvg_career_demo",
                company_id=self.company.pk,
                confirm_synthetic=True,
                verbosity=0,
            )

    @override_settings(DEBUG=True, HORILLA_ENV="production")
    def test_seed_refuses_explicit_production_environment(self):
        with self.assertRaises(CommandError):
            call_command(
                "seed_tvg_career_demo",
                company_id=self.company.pk,
                confirm_synthetic=True,
                verbosity=0,
            )
