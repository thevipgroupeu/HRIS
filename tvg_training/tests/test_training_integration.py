from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from base.models import CompanyGroupAssignment, Department
from employee.models import EmployeeWorkInformation
from horilla.testkit import make_company, make_employee, make_user
from tvg_peoplehub.models import PeopleHubLearningAssignment
from tvg_training.checks import training_integration_configuration_check
from tvg_training.integrations import (
    DisabledTrainingProvider,
    MoodleEnrollmentRequest,
    MoodleProgressRequest,
    MoodleProgressResult,
    MoodleUserRequest,
    SyntheticTrainingProvider,
    TrainingProviderError,
    get_training_provider,
)
from tvg_training.models import (
    MoodleAccountLink,
    MoodleEnrollment,
    StaffIdentityLink,
    StaffIdentitySession,
    StaffRoleMapping,
    TrainingAssignment,
    TrainingCourse,
    TrainingNotification,
    TrainingProgramme,
    TrainingProgrammeCourse,
    TrainingSyncAttempt,
)
from tvg_training.services import (
    approve_staff_role_mapping,
    approve_training_programme,
    assign_training_programmes,
    authorize_authentik_login,
    identify_training_programmes,
    link_staff_identity,
    provision_training_assignment,
    queue_overdue_training_notifications,
    register_staff_session,
    revoke_staff_identity,
    synchronize_training_progress,
)


class TrainingProviderContractTests(SimpleTestCase):
    def setUp(self):
        SyntheticTrainingProvider.reset()

    def tearDown(self):
        SyntheticTrainingProvider.reset()

    def test_synthetic_provider_honors_idempotent_bounded_contract(self):
        provider = SyntheticTrainingProvider()
        user_request = MoodleUserRequest(
            staff_reference="TVG-EMP-2026-0999",
            company_reference="synthetic-company",
            company_email="contract.user@company.example.test",
            first_name="Contract",
            last_name="User",
            idempotency_key="contract:account:1",
        )
        first_account = provider.ensure_account(user_request)
        second_account = provider.ensure_account(user_request)
        self.assertEqual(first_account, second_account)

        enrollment_request = MoodleEnrollmentRequest(
            external_user_id=first_account.external_user_id,
            external_course_id="CONTRACT-COURSE-1",
            due_date=date(2026, 12, 1),
            idempotency_key="contract:enrollment:1",
        )
        first_enrollment = provider.enroll(enrollment_request)
        second_enrollment = provider.enroll(enrollment_request)
        self.assertEqual(first_enrollment, second_enrollment)
        SyntheticTrainingProvider.progress_values[
            first_enrollment.external_enrollment_id
        ] = MoodleProgressResult(
            external_enrollment_id=first_enrollment.external_enrollment_id,
            progress_percent=40,
        )
        progress = provider.progress(
            (
                MoodleProgressRequest(
                    external_enrollment_id=first_enrollment.external_enrollment_id,
                    external_course_id="CONTRACT-COURSE-1",
                ),
            )
        )
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].progress_percent, 40)

    def test_disabled_provider_and_staging_synthetic_guard_fail_closed(self):
        with self.assertRaises(TrainingProviderError):
            DisabledTrainingProvider().ensure_account(object())
        with self.settings(
            TVG_TRAINING_PROVIDER="synthetic",
            HORILLA_ENV="staging",
            TVG_TRAINING_PROVIDER_BACKENDS={
                "synthetic": "tvg_training.integrations.SyntheticTrainingProvider"
            },
        ):
            findings = training_integration_configuration_check(None)
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "Synthetic TrainingHub providers are forbidden",
            ):
                get_training_provider()
        self.assertEqual([item.id for item in findings], ["tvg_training.E002"])


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    LOGIN_URL="peoplehub:login",
    LOGIN_REDIRECT_URL="peoplehub:dashboard",
)
class TrainingIntegrationTests(TestCase):
    def setUp(self):
        SyntheticTrainingProvider.reset()
        self.company = make_company("Synthetic Training Company")
        self.other_company = make_company("Other Training Company", hq=False)
        self.department = Department._base_manager.create(department="Engineering")
        self.department.company_id.add(self.company)

        self.manager_user = make_user("training-manager")
        self.manager = make_employee(
            company=self.company,
            email="manager.personal@example.test",
            first_name="Synthetic",
            last_name="Manager",
            user=self.manager_user,
            department=self.department,
        )
        self.staff_user = make_user("training-staff")
        self.staff = make_employee(
            company=self.company,
            email="staff.personal@example.test",
            first_name="Synthetic",
            last_name="Learner",
            user=self.staff_user,
            department=self.department,
        )
        self.staff.badge_id = "TVG-EMP-2026-0099"
        self.staff.save(update_fields=("badge_id",))
        EmployeeWorkInformation._base_manager.filter(employee_id=self.staff).update(
            email="synthetic.learner@company.example.test",
            reporting_manager_id=self.manager,
        )
        self.staff = (
            type(self.staff)
            ._base_manager.select_related(
                "employee_user_id", "employee_work_info__company_id"
            )
            .get(pk=self.staff.pk)
        )

        permissions = Permission.objects.filter(
            content_type__app_label="tvg_training",
            codename__in={
                "approve_training_programme",
                "assign_training_programme",
                "provision_training",
                "manage_staff_identity",
                "approve_staff_role_mapping",
            },
        )
        self.manager_user.user_permissions.add(*permissions)
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )

    def tearDown(self):
        SyntheticTrainingProvider.reset()

    def _programme(self, *, code="company-onboarding", course_id="SYN-101"):
        programme = TrainingProgramme.all_objects.create(
            company=self.company,
            code=code,
            version=1,
            title="Synthetic company onboarding",
            category=TrainingProgramme.Category.COMPANY_ONBOARDING,
            audience=TrainingProgramme.Audience.ALL,
            default_deadline_days=14,
        )
        course = TrainingCourse.all_objects.create(
            company=self.company,
            external_course_id=course_id,
            title="Synthetic safe-working course",
            launch_url=f"https://moodle.example.test/course/{course_id}",
        )
        TrainingProgrammeCourse.all_objects.create(
            programme=programme,
            course=course,
            sequence=1,
            mandatory=True,
            deadline_days=7,
        )
        return approve_training_programme(user=self.manager_user, programme=programme)

    def _assigned(self):
        programme = self._programme()
        assignments = assign_training_programmes(
            user=self.manager_user, employee=self.staff
        )
        self.assertEqual([item.programme_id for item in assignments], [programme.pk])
        return assignments[0]

    def test_supported_programme_categories_are_explicit(self):
        self.assertEqual(
            set(TrainingProgramme.Category.values),
            {
                "hr_people",
                "fluxops_tech",
                "findmystay_product",
                "findmystay_growth",
                "sales",
                "company_onboarding",
                "it_infrastructure",
            },
        )

    def test_authorized_staff_enrollment_projects_progress_to_peoplehub(self):
        assignment = self._assigned()
        provisioned = provision_training_assignment(
            user=self.manager_user, assignment=assignment
        )
        self.assertEqual(provisioned.status, TrainingAssignment.Status.ACTIVE)
        account = MoodleAccountLink.all_objects.get(employee=self.staff)
        enrollment = MoodleEnrollment.all_objects.get(assignment=assignment)
        projection = enrollment.peoplehub_assignment
        self.assertEqual(account.external_user_id, "synthetic-user-TVG-EMP-2026-0099")
        self.assertEqual(projection.employee_id, self.staff.pk)
        self.assertEqual(projection.provider, "moodle")
        self.assertEqual(projection.external_reference, "SYN-101")
        self.assertEqual(
            SyntheticTrainingProvider.account_calls[0].company_email,
            "synthetic.learner@company.example.test",
        )
        self.assertNotEqual(
            SyntheticTrainingProvider.account_calls[0].company_email,
            self.staff.email,
        )

        completed_at = timezone.now()
        SyntheticTrainingProvider.progress_values[enrollment.external_enrollment_id] = (
            MoodleProgressResult(
                external_enrollment_id=enrollment.external_enrollment_id,
                progress_percent=100,
                completed_at=completed_at,
            )
        )
        synchronized = synchronize_training_progress(
            user=self.manager_user, assignment=assignment
        )
        enrollment.refresh_from_db()
        projection.refresh_from_db()
        self.assertEqual(synchronized.status, TrainingAssignment.Status.COMPLETED)
        self.assertEqual(enrollment.status, MoodleEnrollment.Status.COMPLETED)
        self.assertEqual(
            projection.status, PeopleHubLearningAssignment.Status.COMPLETED
        )
        self.assertEqual(projection.progress_percent, 100)

        client = Client()
        client.force_login(self.staff_user)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Synthetic safe-working course")
        self.assertContains(response, "100%")
        self.assertContains(response, projection.launch_url)
        self.assertNotContains(response, "manager.personal@example.test")

    def test_provisioning_retry_is_idempotent_in_horilla(self):
        assignment = self._assigned()
        first = provision_training_assignment(
            user=self.manager_user, assignment=assignment
        )
        first_enrollment = MoodleEnrollment.all_objects.get(assignment=first)
        second = provision_training_assignment(user=self.manager_user, assignment=first)
        second_enrollment = MoodleEnrollment.all_objects.get(assignment=second)
        self.assertEqual(first_enrollment.pk, second_enrollment.pk)
        self.assertEqual(
            first_enrollment.idempotency_key, second_enrollment.idempotency_key
        )
        self.assertEqual(MoodleAccountLink.all_objects.count(), 1)
        self.assertEqual(MoodleEnrollment.all_objects.count(), 1)
        self.assertEqual(PeopleHubLearningAssignment.all_objects.count(), 1)
        self.assertEqual(
            SyntheticTrainingProvider.enrollment_calls[0].idempotency_key,
            SyntheticTrainingProvider.enrollment_calls[1].idempotency_key,
        )

    @override_settings(TVG_TRAINING_PROVIDER="disabled")
    def test_disabled_provider_fails_closed_with_reviewable_attempt(self):
        assignment = self._assigned()
        with self.assertRaisesMessage(
            ValidationError, "No authorized Moodle provisioning adapter"
        ):
            provision_training_assignment(user=self.manager_user, assignment=assignment)
        assignment.refresh_from_db()
        attempt = TrainingSyncAttempt.all_objects.get(assignment=assignment)
        self.assertEqual(assignment.status, TrainingAssignment.Status.FAILED)
        self.assertEqual(attempt.outcome, TrainingSyncAttempt.Outcome.FAILED)
        self.assertFalse(MoodleAccountLink.all_objects.exists())

    def test_unapproved_or_cross_company_training_is_not_assignable(self):
        TrainingProgramme.all_objects.create(
            company=self.company,
            code="draft",
            version=1,
            title="Unapproved draft",
            category=TrainingProgramme.Category.HR_PEOPLE,
            audience=TrainingProgramme.Audience.ALL,
        )
        other_programme = TrainingProgramme.all_objects.create(
            company=self.other_company,
            code="other-company",
            version=1,
            title="Other company programme",
            category=TrainingProgramme.Category.IT_INFRASTRUCTURE,
            audience=TrainingProgramme.Audience.ALL,
        )
        self.assertFalse(identify_training_programmes(self.staff).exists())
        with self.assertRaises(PermissionDenied):
            approve_training_programme(
                user=self.manager_user, programme=other_programme
            )
        self._programme()
        unauthorized_user = make_user("unauthorized-training-manager")
        make_employee(
            company=self.company,
            email="unauthorized.manager@example.test",
            user=unauthorized_user,
        )
        with self.assertRaises(PermissionDenied):
            assign_training_programmes(user=unauthorized_user, employee=self.staff)

    def test_mandatory_overdue_training_is_visible_only_to_supervisor(self):
        assignment = self._assigned()
        provision_training_assignment(user=self.manager_user, assignment=assignment)
        enrollment = MoodleEnrollment.all_objects.get(assignment=assignment)
        enrollment.due_date = timezone.localdate() - timedelta(days=1)
        enrollment.save(update_fields=("due_date",))
        outsider_user = make_user("other-company-supervisor")
        outsider = make_employee(
            company=self.other_company,
            email="other.supervisor@example.test",
            user=outsider_user,
        )
        EmployeeWorkInformation._base_manager.filter(employee_id=self.staff).update(
            reporting_manager_id=outsider
        )
        self.assertEqual(queue_overdue_training_notifications(), [])
        EmployeeWorkInformation._base_manager.filter(employee_id=self.staff).update(
            reporting_manager_id=self.manager
        )
        created = queue_overdue_training_notifications()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].supervisor_id, self.manager.pk)
        self.assertEqual(queue_overdue_training_notifications(), [])
        self.assertEqual(TrainingNotification.all_objects.count(), 1)

        manager_client = Client()
        manager_client.force_login(self.manager_user)
        response = manager_client.get("/")
        self.assertContains(response, "Overdue mandatory training")
        self.assertContains(response, "Synthetic safe-working course")

        staff_client = Client()
        staff_client.force_login(self.staff_user)
        response = staff_client.get("/")
        self.assertNotContains(response, "Overdue mandatory training")

    def test_authentik_roles_require_human_mapping_and_local_authorization(self):
        identity = link_staff_identity(
            user=self.manager_user,
            employee=self.staff,
            subject="synthetic-authentik-subject-1",
        )
        group = Group.objects.create(name="Synthetic Training Supervisors")
        mapping = StaffRoleMapping.all_objects.create(
            company=self.company,
            external_role="training-supervisor",
            group=group,
        )
        approve_staff_role_mapping(user=self.manager_user, mapping=mapping)

        before_groups = set(self.staff_user.groups.values_list("pk", flat=True))
        authorization = authorize_authentik_login(
            user=self.staff_user,
            subject=identity.subject,
            external_roles=("training-supervisor", "unknown-role"),
        )
        self.assertEqual(authorization.authorized_roles, ())
        self.assertEqual(
            set(self.staff_user.groups.values_list("pk", flat=True)), before_groups
        )

        CompanyGroupAssignment.objects.create(
            user=self.staff_user, company=self.company, group=group
        )
        authorization = authorize_authentik_login(
            user=self.staff_user,
            subject=identity.subject,
            external_roles=("training-supervisor", "unknown-role"),
        )
        self.assertEqual(authorization.authorized_roles, ("training-supervisor",))
        with self.assertRaises(PermissionDenied):
            authorize_authentik_login(
                user=self.staff_user,
                subject=identity.subject,
                external_roles=(),
                account_enabled=False,
            )

        self.staff_user.is_active = False
        self.staff_user.save(update_fields=("is_active",))
        with self.assertRaises(PermissionDenied):
            authorize_authentik_login(
                user=self.staff_user,
                subject=identity.subject,
                external_roles=("training-supervisor",),
            )
        self.staff_user.is_active = True
        self.staff_user.save(update_fields=("is_active",))
        identity.status = StaffIdentityLink.Status.DISABLED
        identity.save(update_fields=("status",))
        with self.assertRaises(PermissionDenied):
            authorize_authentik_login(
                user=self.staff_user,
                subject=identity.subject,
                external_roles=("training-supervisor",),
            )

    def test_identity_revocation_terminates_tracked_peoplehub_session(self):
        identity = link_staff_identity(
            user=self.manager_user,
            employee=self.staff,
            subject="synthetic-authentik-subject-2",
        )
        client = Client()
        client.force_login(self.staff_user)
        session_key = client.session.session_key
        manager_client = Client()
        manager_client.force_login(self.manager_user)
        with self.assertRaises(PermissionDenied):
            register_staff_session(
                user=self.staff_user,
                session_key=manager_client.session.session_key,
                provider_session_id="wrong-user-session",
            )
        register_staff_session(
            user=self.staff_user,
            session_key=session_key,
            provider_session_id="synthetic-provider-session",
        )
        record = StaffIdentitySession.all_objects.get(identity=identity)
        self.assertNotEqual(record.session_key_hash, session_key)
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

        revoked = revoke_staff_identity(
            user=self.manager_user,
            identity=identity,
            reason="Synthetic access removal test",
        )
        record.refresh_from_db()
        self.assertEqual(revoked.status, StaffIdentityLink.Status.REVOKED)
        self.assertEqual(revoked.session_version, 2)
        self.assertIsNotNone(record.revoked_at)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertEqual(client.get("/").status_code, 302)

    @override_settings(ROOT_URLCONF="horilla.careerhub_urls")
    def test_careerhub_urlconf_never_exposes_staff_training_or_peoplehub(self):
        with self.assertRaises(Resolver404):
            resolve("/training/")
        with self.assertRaises(Resolver404):
            resolve("/directory/")
