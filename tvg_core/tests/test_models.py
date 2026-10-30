from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from horilla.testkit import make_company, make_employee, make_user
from tvg_core.models import Person
from tvg_core.services import link_person_to_employee
from tvg_core.tests.factories import make_person, make_venture


class VentureAndPersonTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Legal Employer")

    def test_venture_keeps_brand_distinct_from_legal_employer(self):
        venture = make_venture(
            company=self.company,
            name="FluxOps",
            slug="fluxops",
        )

        self.assertEqual(venture.name, "FluxOps")
        self.assertEqual(venture.legal_employer, self.company)
        self.assertNotEqual(venture.name, venture.legal_employer.company)

    def test_person_normalizes_email_and_rejects_case_insensitive_duplicate(self):
        person = make_person(company=self.company, email="Person@Example.Test")
        self.assertEqual(person.primary_email, "person@example.test")

        with self.assertRaises(ValidationError):
            make_person(company=self.company, email="PERSON@example.test")

    def test_person_rejects_employee_from_another_legal_employer(self):
        other_company = make_company("Synthetic Other Employer", hq=False)
        employee = make_employee(
            company=other_company,
            email="other-employee@example.test",
        )

        with self.assertRaises(ValidationError):
            make_person(
                company=self.company,
                email="identity@example.test",
                employee=employee,
            )

    def test_employee_identity_link_requires_explicit_permission(self):
        person = make_person(company=self.company)
        employee = make_employee(
            company=self.company,
            email="linked-employee@example.test",
        )
        actor = make_user("synthetic_identity_linker")
        make_employee(
            company=self.company,
            email="identity-linker@example.test",
            user=actor,
        )

        with self.assertRaises(PermissionDenied):
            link_person_to_employee(actor=actor, person=person, employee=employee)

        permission = Permission.objects.get(
            content_type__app_label="tvg_core",
            codename="link_person_employee",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)

        linked = link_person_to_employee(
            actor=actor,
            person=person,
            employee=employee,
        )
        self.assertEqual(linked.employee, employee)
        self.assertEqual(
            Person.all_objects.get(pk=person.pk).employee,
            employee,
        )

    def test_employee_identity_link_permission_does_not_cross_employers(self):
        person = make_person(company=self.company)
        employee = make_employee(
            company=self.company,
            email="same-company-employee@example.test",
        )
        other_company = make_company("Synthetic Identity Actor Employer", hq=False)
        actor = make_user("synthetic_cross_company_identity_linker")
        make_employee(
            company=other_company,
            email="cross-company-identity-linker@example.test",
            user=actor,
        )
        permission = Permission.objects.get(
            content_type__app_label="tvg_core",
            codename="link_person_employee",
        )
        actor.user_permissions.add(permission)
        actor = type(actor).objects.get(pk=actor.pk)

        with self.assertRaises(PermissionDenied):
            link_person_to_employee(actor=actor, person=person, employee=employee)
