from django.test import TestCase

from horilla.testkit import make_company
from tvg_careers.forms import JobOpeningAdminForm
from tvg_careers.tests.factories import make_job_opening
from tvg_core.forms import PersonAdminForm, VentureAdminForm
from tvg_core.tests.factories import make_venture


class TvgAdminFormTests(TestCase):
    def test_forms_expose_owned_and_reused_fields(self):
        company = make_company("Synthetic Admin Employer")
        venture = make_venture(
            company=company,
            name="THEVIPGROUP",
            slug="thevipgroup",
        )
        opening = make_job_opening(company=company, venture=venture)

        opening_form = JobOpeningAdminForm(instance=opening)
        self.assertIn("publication_state", opening_form.fields)
        self.assertIn("required_skills", opening_form.fields)
        self.assertIn("preferred_skills", opening_form.fields)
        self.assertIn("hiring_managers", opening_form.fields)
        self.assertIn("survey_templates", opening_form.fields)
        self.assertIn("number_of_positions", opening_form.fields)
        self.assertIn("legal_employer", VentureAdminForm().fields)
        self.assertIn("employee", PersonAdminForm().fields)
