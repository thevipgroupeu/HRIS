from datetime import date

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from base.models import Department, EmployeeType, JobPosition, WorkType
from employee.models import EmployeeWorkInformation, Policy
from helpdesk.models import Ticket, TicketType
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.factories import make_available_leave, make_leave_type
from tvg_core.models import Venture
from tvg_peoplehub.models import (
    EmployeeVentureAssignment,
    PeopleHubLearningAssignment,
    PeopleHubRequestType,
)
from tvg_peoplehub.services import approve_request_type, directory_rows, submit_request


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    LOGIN_URL="peoplehub:login",
    LOGIN_REDIRECT_URL="peoplehub:dashboard",
)
class PeopleHubPortalTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Portal Company")
        self.other_company = make_company("Other Synthetic Company", hq=False)
        self.department = Department._base_manager.create(department="Engineering")
        self.department.company_id.add(self.company)
        self.position = JobPosition._base_manager.create(
            job_position="Software Intern", department_id=self.department
        )
        self.position.company_id.add(self.company)
        self.work_type = WorkType._base_manager.create(work_type="Hybrid")
        self.work_type.company_id.add(self.company)
        self.intern_type = EmployeeType._base_manager.create(employee_type="Intern")
        self.intern_type.company_id.add(self.company)
        self.venture = Venture.all_objects.create(
            legal_employer=self.company,
            name="Synthetic Venture",
            slug="synthetic-portal",
        )

        self.manager_user = make_user("portal-manager")
        self.manager = make_employee(
            company=self.company,
            email="manager.private@example.test",
            first_name="Synthetic",
            last_name="Manager",
            user=self.manager_user,
            department=self.department,
        )
        self.intern_user = make_user("portal-intern")
        self.intern = make_employee(
            company=self.company,
            email="intern.private@example.test",
            first_name="Synthetic",
            last_name="Intern",
            phone="+48111111111",
            user=self.intern_user,
            department=self.department,
            work_type=self.work_type,
            date_joining=date(2026, 11, 2),
        )
        self.intern.badge_id = "TVG-INT-2026-0001"
        self.intern.address = "PRIVATE HOME ADDRESS"
        self.intern.save(update_fields=("badge_id", "address"))
        EmployeeWorkInformation._base_manager.filter(employee_id=self.intern).update(
            job_position_id=self.position,
            reporting_manager_id=self.manager,
            employee_type_id=self.intern_type,
            email="synthetic.intern@company.example.test",
            location="Kraków",
            contract_end_date=date(2027, 2, 2),
        )
        self.intern = (
            EmployeeWorkInformation._meta.get_field("employee_id")
            .remote_field.model._base_manager.select_related("employee_work_info")
            .get(pk=self.intern.pk)
        )
        EmployeeVentureAssignment.all_objects.create(
            employee=self.intern,
            venture=self.venture,
            assigned_by=self.manager,
        )

        self.peer_user = make_user("portal-peer")
        self.peer = make_employee(
            company=self.company,
            email="peer.personal.secret@example.test",
            first_name="Synthetic",
            last_name="Peer",
            phone="+48222222222",
            user=self.peer_user,
            department=self.department,
        )
        self.peer.address = "PEER PRIVATE ADDRESS"
        self.peer.save(update_fields=("address",))
        EmployeeWorkInformation._base_manager.filter(employee_id=self.peer).update(
            email="peer@company.example.test",
            job_position_id=self.position,
            location="Remote",
        )
        self.peer = (
            type(self.peer)
            ._base_manager.select_related("employee_work_info")
            .get(pk=self.peer.pk)
        )

        self.outsider_user = make_user("portal-outsider")
        self.outsider = make_employee(
            company=self.other_company,
            email="outsider.private@example.test",
            first_name="Other",
            last_name="Tenant",
            user=self.outsider_user,
        )
        EmployeeWorkInformation._base_manager.filter(employee_id=self.outsider).update(
            email="outsider@other.example.test"
        )
        self.client = Client()

    def test_intern_dashboard_uses_native_data_without_admin_access(self):
        leave_type = make_leave_type("Synthetic annual leave", 20)
        make_available_leave(employee=self.intern, leave_type=leave_type, available=12)
        policy = Policy._base_manager.create(
            title="Synthetic company policy",
            body="Synthetic policy body",
            is_visible_to_all=True,
        )
        policy.company_id.add(self.company)
        PeopleHubLearningAssignment.all_objects.create(
            employee=self.intern,
            kind=PeopleHubLearningAssignment.Kind.MOODLE,
            title="Synthetic Moodle course",
            provider="moodle",
            external_reference="SYN-COURSE-001",
            assigned_by=self.manager,
        )
        PeopleHubLearningAssignment.all_objects.create(
            employee=self.intern,
            kind=PeopleHubLearningAssignment.Kind.WEEKLY_REPORT,
            title="Synthetic weekly report",
            assigned_by=self.manager,
        )

        self.client.force_login(self.intern_user)
        response = self.client.get(reverse("peoplehub:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TVG-INT-2026-0001")
        self.assertContains(response, "synthetic.intern@company.example.test")
        self.assertContains(response, "Synthetic company policy")
        self.assertContains(response, "Synthetic Venture")
        self.assertContains(response, "Synthetic Moodle course")
        self.assertContains(response, "Synthetic weekly report")
        self.assertNotContains(response, "peer.personal.secret@example.test")
        self.assertNotContains(response, "My team")
        self.assertNotContains(response, "Request queue")
        self.assertEqual(self.client.get("/admin/").status_code, 404)
        self.assertEqual(self.client.get("/employee/employee-view/").status_code, 404)
        self.assertEqual(
            self.client.get(reverse("peoplehub:request-queue")).status_code, 403
        )
        profile = self.client.get(reverse("peoplehub:my-profile"))
        self.assertEqual(profile.status_code, 200)
        self.assertContains(profile, "intern.private@example.test")
        self.assertContains(profile, "PRIVATE HOME ADDRESS")
        self.assertNotContains(profile, "peer.personal.secret@example.test")

    def test_supervisor_and_it_permissions_drive_navigation(self):
        self.manager_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="helpdesk", codename="view_ticket"
            )
        )
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse("peoplehub:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My team")
        self.assertContains(response, "Request queue")

    def test_directory_is_company_scoped_and_uses_exact_allowlist(self):
        rows = directory_rows(self.intern)
        self.assertTrue(rows)
        self.assertEqual(
            set(rows[0]),
            {
                "name",
                "company_email",
                "position",
                "department",
                "venture",
                "manager",
                "work_location",
            },
        )
        self.client.force_login(self.intern_user)
        response = self.client.get(reverse("peoplehub:directory"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "peer@company.example.test")
        self.assertNotContains(response, "peer.personal.secret@example.test")
        self.assertNotContains(response, "PEER PRIVATE ADDRESS")
        self.assertNotContains(response, "+48222222222")
        self.assertNotContains(response, "outsider@other.example.test")

    def test_manager_can_view_direct_report_but_not_unrelated_employee(self):
        self.client.force_login(self.manager_user)
        allowed = self.client.get(
            reverse("peoplehub:team-member", kwargs={"pk": self.intern.pk})
        )
        denied = self.client.get(
            reverse("peoplehub:team-member", kwargs={"pk": self.peer.pk})
        )
        cross_tenant = self.client.get(
            reverse("peoplehub:team-member", kwargs={"pk": self.outsider.pk})
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(cross_tenant.status_code, 403)

    def test_hr_permission_allows_company_team_only(self):
        self.manager_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="employee", codename="view_employee"
            )
        )
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )
        self.client.force_login(self.manager_user)
        self.assertEqual(
            self.client.get(
                reverse("peoplehub:team-member", kwargs={"pk": self.peer.pk})
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("peoplehub:team-member", kwargs={"pk": self.outsider.pk})
            ).status_code,
            403,
        )

    def _request_type(self, *, company=None, department=None, approver=None):
        company = company or self.company
        department = department or self.department
        approver = approver or self.manager
        ticket_type = TicketType._base_manager.create(
            title=f"Synthetic HR {company.pk}",
            type="service_request",
            prefix="SYN",
            company_id=company,
        )
        return PeopleHubRequestType.all_objects.create(
            company=company,
            code=f"synthetic-hr-{company.pk}",
            title="HR question",
            category=PeopleHubRequestType.Category.HR,
            native_ticket_type=ticket_type,
            target_department=department,
            approved_by=approver,
            approved_at=timezone.now(),
        )

    def test_internal_request_reuses_native_ticket_and_enforces_company(self):
        request_type = self._request_type()
        self.client.force_login(self.intern_user)
        response = self.client.post(
            reverse("peoplehub:request-create"),
            {
                "request_type": request_type.pk,
                "title": "Synthetic HR question",
                "description": "Synthetic, non-personal test content.",
                "priority": "low",
            },
        )
        self.assertRedirects(response, reverse("peoplehub:dashboard"))
        ticket = Ticket._base_manager.get(title="Synthetic HR question")
        self.assertEqual(ticket.employee_id_id, self.intern.pk)
        self.assertEqual(ticket.peoplehub_request_link.request_type_id, request_type.pk)

        other_department = Department._base_manager.create(department="Other HR")
        other_department.company_id.add(self.other_company)
        other_type = self._request_type(
            company=self.other_company,
            department=other_department,
            approver=self.outsider,
        )
        with self.assertRaises(PermissionDenied):
            submit_request(
                employee=self.intern,
                request_type=other_type,
                title="Blocked cross-tenant request",
                description="Synthetic",
                priority="low",
            )

    def test_request_type_requires_authorized_approval_and_is_versioned_by_replacement(
        self,
    ):
        ticket_type = TicketType._base_manager.create(
            title="Synthetic IT approval",
            type="service_request",
            prefix="SIT",
            company_id=self.company,
        )
        request_type = PeopleHubRequestType.all_objects.create(
            company=self.company,
            code="synthetic-it-approval",
            title="IT support",
            category=PeopleHubRequestType.Category.IT,
            native_ticket_type=ticket_type,
            target_department=self.department,
        )
        with self.assertRaises(PermissionDenied):
            approve_request_type(user=self.manager_user, request_type=request_type)

        self.manager_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tvg_peoplehub",
                codename="approve_request_type",
            )
        )
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )
        approved = approve_request_type(
            user=self.manager_user, request_type=request_type
        )
        self.assertEqual(approved.approved_by_id, self.manager.pk)
        approved.title = "Silently changed route"
        with self.assertRaises(ValidationError):
            approved.save()

    def test_leave_request_uses_native_leave_record_for_authenticated_employee(self):
        leave_type = make_leave_type("Synthetic portal leave", 20)
        balance = make_available_leave(
            employee=self.intern, leave_type=leave_type, available=12
        )
        type(balance)._base_manager.filter(pk=balance.pk).update(total_leave_days=12)
        self.client.force_login(self.intern_user)
        response = self.client.post(
            reverse("peoplehub:leave-create"),
            {
                "leave_type_id": leave_type.pk,
                "start_date": "2026-12-01",
                "start_date_breakdown": "full_day",
                "end_date": "2026-12-01",
                "end_date_breakdown": "full_day",
                "description": "Synthetic leave request",
            },
        )
        self.assertRedirects(response, reverse("peoplehub:dashboard"))
        self.assertTrue(
            self.intern.leaverequest_set.filter(
                description="Synthetic leave request", status="requested"
            ).exists()
        )

    def test_non_employee_cannot_authenticate_to_peoplehub(self):
        user = make_user("not-an-employee", password="synthetic-password")
        response = self.client.post(
            reverse("peoplehub:login"),
            {"username": user.username, "password": "synthetic-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
