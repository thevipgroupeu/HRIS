from __future__ import annotations

from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from asset.models import Asset, AssetCategory
from base.models import CompanyGroupAssignment
from horilla.testkit import make_company, make_employee, make_user
from onboarding.models import CandidateTask
from tvg_erasmus.models import AcceptanceLetter, ErasmusCase
from tvg_erasmus.services import (
    confirm_acceptance_receipt,
    issue_acceptance_letter,
    send_acceptance_letter,
)
from tvg_erasmus.tests import test_acceptance_workflow as acceptance_helpers
from tvg_onboarding.models import (
    AssetAcknowledgmentTemplate,
    InternOnboarding,
    OnboardingAccessProfile,
    OnboardingAssetAllocation,
    OnboardingPolicy,
    StaffIdSequence,
)
from tvg_onboarding.services import (
    _next_identifier,
    allocate_asset,
    approve_access_profile,
    approve_asset_template,
    approve_intern_onboarding,
    complete_checklist_item,
    complete_intern_onboarding,
    complete_manual_provisioning,
    create_employee_for_onboarding,
    grant_onboarding_access,
    prepare_asset_acknowledgment,
    record_asset_acknowledgment,
    start_intern_onboarding,
)


class InternOnboardingTests(TestCase):
    _restore_storage = acceptance_helpers.AcceptanceWorkflowTests._restore_storage
    _staff = acceptance_helpers.AcceptanceWorkflowTests._staff
    _verify_application = acceptance_helpers.AcceptanceWorkflowTests._verify_application
    _select_application = acceptance_helpers.AcceptanceWorkflowTests._select_application
    _case_values = acceptance_helpers.AcceptanceWorkflowTests._case_values
    _create_case = acceptance_helpers.AcceptanceWorkflowTests._create_case
    _approved_case = acceptance_helpers.AcceptanceWorkflowTests._approved_case
    _template = acceptance_helpers.AcceptanceWorkflowTests._template

    def setUp(self):
        acceptance_helpers.AcceptanceWorkflowTests.setUp(self)
        codenames = {
            "start_intern_onboarding",
            "approve_intern_onboarding",
            "manage_intern_onboarding",
            "complete_intern_onboarding",
            "manage_onboarding_access",
            "manage_account_provisioning",
            "allocate_onboarding_asset",
            "approve_onboarding_access_profile",
            "approve_asset_ack_template",
            "view_internonboarding",
        }
        self.manager_user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="tvg_onboarding", codename__in=codenames
            ),
            Permission.objects.get(
                content_type__app_label="asset", codename="add_assetassignment"
            ),
        )
        self.manager_user = type(self.manager_user).objects.get(pk=self.manager_user.pk)

        self.ack_media = TemporaryDirectory()
        self.addCleanup(self.ack_media.cleanup)
        self.ack_field = OnboardingAssetAllocation._meta.get_field("pdf_file")
        self.original_ack_storage = self.ack_field.storage
        self.ack_field.storage = FileSystemStorage(location=self.ack_media.name)
        self.addCleanup(self._restore_ack_storage)

    def _restore_ack_storage(self):
        self.ack_field.storage = self.original_ack_storage

    def _trigger_ready_case(self, case=None):
        case = case or self._approved_case()
        letter = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=self._template(),
            issue_date=date(2026, 10, 27),
        )
        send_acceptance_letter(user=self.manager_user, letter=letter)
        confirm_acceptance_receipt(
            case_id=case.public_id,
            letter_id=letter.public_id,
            person_public_id=self.person.public_id,
        )
        case.refresh_from_db()
        case.before_mobility_completed_at = timezone.now()
        case.before_mobility_completed_by = self.manager
        case.save(
            update_fields=(
                "before_mobility_completed_at",
                "before_mobility_completed_by",
            )
        )
        return case

    def _onboarding(self):
        return start_intern_onboarding(
            user=self.manager_user, case=self._trigger_ready_case()
        )

    def _approve_and_convert(self, onboarding):
        approve_intern_onboarding(user=self.manager_user, onboarding=onboarding)
        complete_manual_provisioning(
            user=self.manager_user,
            onboarding=onboarding,
            work_email="synthetic.intern@company.example.test",
            external_reference="SYNTHETIC-IT-001",
        )
        return create_employee_for_onboarding(
            user=self.manager_user, onboarding=onboarding, phone="+48111111111"
        )

    def _access_profile(self):
        group = Group.objects.create(name="Synthetic Intern Venture Access")
        profile = OnboardingAccessProfile.all_objects.create(
            company=self.company,
            name="Synthetic Intern Access",
            venture=self.venture,
            department=self.opening.job_position.department_id,
        )
        profile.groups.add(group)
        approve_access_profile(user=self.manager_user, profile=profile)
        return profile, group

    def _asset(self):
        category = AssetCategory._base_manager.create(
            asset_category_name="Synthetic Phase 11 Laptops",
            asset_category_description="Synthetic equipment only.",
        )
        category.company_id.add(self.company)
        return Asset._base_manager.create(
            asset_name="Synthetic Test Laptop",
            asset_tracking_id="SYN-P11-LAPTOP-001",
            asset_purchase_date=date(2026, 1, 1),
            asset_purchase_cost="1000.00",
            asset_category_id=category,
            quantity=1,
        )

    def _asset_template(self):
        template = AssetAcknowledgmentTemplate.all_objects.create(
            company=self.company,
            name="Synthetic Asset Allocation Form",
            version_number=1,
            body=(
                "{company_name} allocates {asset_name}, serial {asset_serial_number}, "
                "to {employee_name} ({intern_id}/{employee_badge_id}) on {allocation_date}."
            ),
        )
        return approve_asset_template(user=self.manager_user, template=template)

    def test_trigger_and_hr_approval_are_required(self):
        case = self._approved_case()
        with self.assertRaises(ValidationError):
            start_intern_onboarding(user=self.manager_user, case=case)

        onboarding = start_intern_onboarding(
            user=self.manager_user, case=self._trigger_ready_case(case)
        )
        self.assertEqual(onboarding.status, InternOnboarding.Status.PENDING_APPROVAL)
        self.assertEqual(onboarding.checklist_items.count(), 15)
        self.assertEqual(onboarding.communications.count(), 2)
        with self.assertRaises(ValidationError):
            complete_manual_provisioning(
                user=self.manager_user,
                onboarding=onboarding,
                work_email="too.early@company.example.test",
            )

    def test_employee_conversion_is_idempotent_and_preserves_identity_history(self):
        onboarding = self._onboarding()
        employee = self._approve_and_convert(onboarding)
        repeated_provisioning = complete_manual_provisioning(
            user=self.manager_user,
            onboarding=onboarding,
            work_email="synthetic.intern@company.example.test",
            external_reference="SYNTHETIC-IT-RETRY",
        )
        repeated = create_employee_for_onboarding(
            user=self.manager_user, onboarding=onboarding, phone="+48111111111"
        )
        self.assertEqual(
            repeated_provisioning.provisioned_email,
            "synthetic.intern@company.example.test",
        )
        self.assertEqual(repeated.pk, employee.pk)
        self.person.refresh_from_db()
        self.application.candidate.refresh_from_db()
        self.assertEqual(self.person.employee_id, employee.pk)
        self.assertEqual(
            self.application.candidate.converted_employee_id_id, employee.pk
        )
        self.assertEqual(self.person.primary_email, "acceptance.candidate@example.test")
        self.assertEqual(employee.email, "synthetic.intern@company.example.test")

    def test_staff_ids_are_unique_and_use_start_year(self):
        onboarding = self._onboarding()
        self.assertRegex(onboarding.intern_id, r"^TVG-INT-2027-\d{4}$")
        self.assertRegex(onboarding.employee_badge_id, r"^TVG-EMP-2027-\d{4}$")
        policy = OnboardingPolicy.all_objects.get(company=self.company)
        next_intern = _next_identifier(
            policy=policy, year=2027, kind=StaffIdSequence.Kind.INTERN
        )
        next_employee = _next_identifier(
            policy=policy, year=2027, kind=StaffIdSequence.Kind.EMPLOYEE
        )
        self.assertNotEqual(next_intern, onboarding.intern_id)
        self.assertNotEqual(next_employee, onboarding.employee_badge_id)

    def test_permissions_and_company_scoped_access_are_enforced(self):
        case = self._trigger_ready_case()
        outsider_user = make_user("synthetic-p11-outsider")
        make_employee(
            company=self.company,
            email="synthetic-p11-outsider.employee@example.test",
            user=outsider_user,
        )
        with self.assertRaises(PermissionDenied):
            start_intern_onboarding(user=outsider_user, case=case)

        other_company = make_company("Synthetic Other Onboarding Employer")
        cross_tenant_user = make_user("synthetic-p11-cross-tenant")
        make_employee(
            company=other_company,
            email="synthetic-p11-cross-tenant.employee@example.test",
            user=cross_tenant_user,
        )
        cross_tenant_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tvg_onboarding",
                codename="start_intern_onboarding",
            )
        )
        cross_tenant_user = type(cross_tenant_user).objects.get(pk=cross_tenant_user.pk)
        with self.assertRaises(PermissionDenied):
            start_intern_onboarding(user=cross_tenant_user, case=case)

        onboarding = start_intern_onboarding(user=self.manager_user, case=case)
        self._approve_and_convert(onboarding)
        profile, group = self._access_profile()
        grant_onboarding_access(
            user=self.manager_user, onboarding=onboarding, profile=profile
        )
        onboarding.refresh_from_db()
        self.assertTrue(
            CompanyGroupAssignment.objects.filter(
                user=onboarding.employee.employee_user_id,
                company=self.company,
                group=group,
            ).exists()
        )

    def test_native_asset_allocation_requires_both_permissions(self):
        onboarding = self._onboarding()
        self._approve_and_convert(onboarding)
        asset = self._asset()
        self.manager_user.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="asset", codename="add_assetassignment"
            )
        )
        self.manager_user = type(self.manager_user).objects.get(pk=self.manager_user.pk)
        with self.assertRaises(PermissionDenied):
            allocate_asset(user=self.manager_user, onboarding=onboarding, asset=asset)

    def test_staff_detail_is_hidden_without_view_permission(self):
        onboarding = self._onboarding()
        outsider_user = make_user("synthetic-p11-detail-outsider")
        make_employee(
            company=self.company,
            email="synthetic-p11-detail-outsider.employee@example.test",
            user=outsider_user,
        )
        client = Client()
        client.force_login(outsider_user)
        response = client.get(
            reverse("tvg_onboarding:detail", args=(onboarding.public_id,))
        )
        self.assertEqual(response.status_code, 404)

        client.force_login(self.manager_user)
        response = client.get(
            reverse("tvg_onboarding:detail", args=(onboarding.public_id,))
        )
        self.assertEqual(response.status_code, 200)

    def test_fake_accepted_candidate_becomes_onboarded_intern(self):
        onboarding = self._onboarding()
        employee = self._approve_and_convert(onboarding)
        profile, group = self._access_profile()
        grant_onboarding_access(
            user=self.manager_user, onboarding=onboarding, profile=profile
        )
        allocation = allocate_asset(
            user=self.manager_user, onboarding=onboarding, asset=self._asset()
        )
        allocation = prepare_asset_acknowledgment(
            user=self.manager_user,
            allocation=allocation,
            template=self._asset_template(),
        )
        self.assertTrue(allocation.pdf_file.name.endswith(".pdf"))
        record_asset_acknowledgment(
            user=self.manager_user,
            allocation=allocation,
            reference="SYNTHETIC-ACK-001",
        )
        for link in onboarding.checklist_items.exclude(native_task__status="done"):
            complete_checklist_item(
                user=self.manager_user,
                onboarding=onboarding,
                code=link.code,
                evidence_note=f"Synthetic completion for {link.code}.",
            )
        completed = complete_intern_onboarding(
            user=self.manager_user, onboarding=onboarding
        )
        completed.refresh_from_db()
        self.application.candidate.refresh_from_db()
        self.assertEqual(completed.status, InternOnboarding.Status.ONBOARDED)
        self.assertEqual(completed.employee_id, employee.pk)
        self.assertTrue(self.application.candidate.converted)
        self.assertEqual(
            CandidateTask._base_manager.filter(
                candidate_id=self.application.candidate, status="done"
            ).count(),
            15,
        )
        self.assertTrue(employee.employee_user_id.groups.filter(pk=group.pk).exists())
        self.assertEqual(
            completed.asset_allocations.get().native_assignment.asset_item_id.status,
            "In use",
        )
