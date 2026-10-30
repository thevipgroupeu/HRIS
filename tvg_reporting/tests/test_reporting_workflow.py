from __future__ import annotations

from datetime import date, time, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from base.models import Department, EmployeeType
from employee.models import EmployeeWorkInformation
from horilla.testkit import make_company, make_employee, make_user
from project.models import Project, Task
from tvg_reporting.models import (
    MonthlyPresentation,
    MonthlyPresentationVersion,
    ReportingAIAnalysis,
    ReportingEvent,
    ReportingNotification,
    ReportingPolicy,
    ReportingRubricVersion,
    ReportStatus,
    ReviewOutcome,
    WeeklyReport,
)
from tvg_reporting.services import (
    approve_rubric,
    can_view_item,
    dispatch_due_notifications,
    management_summary,
    request_ai_analysis,
    review_monthly_presentation,
    review_weekly_report,
    save_monthly_draft,
    save_weekly_draft,
    submit_monthly_presentation,
    submit_weekly_report,
)


@override_settings(
    ROOT_URLCONF="horilla.peoplehub_urls",
    LOGIN_URL="peoplehub:login",
    LOGIN_REDIRECT_URL="peoplehub:dashboard",
)
class InternshipReportingTests(TestCase):
    def setUp(self):
        self.company = make_company("Synthetic Reporting Company")
        self.other_company = make_company("Synthetic Other Company", hq=False)
        self.department = Department._base_manager.create(
            department="Synthetic Delivery"
        )
        self.department.company_id.add(self.company)
        self.intern_type = EmployeeType._base_manager.create(employee_type="Intern")
        self.intern_type.company_id.add(self.company)

        self.manager_user = make_user("report-manager")
        self.manager = make_employee(
            company=self.company,
            email="manager@example.test",
            first_name="Synthetic",
            last_name="Manager",
            user=self.manager_user,
            department=self.department,
        )
        self.intern_user = make_user("report-intern")
        self.intern = make_employee(
            company=self.company,
            email="intern@example.test",
            first_name="Synthetic",
            last_name="Intern",
            user=self.intern_user,
            department=self.department,
            date_joining=date(2026, 11, 2),
        )
        EmployeeWorkInformation._base_manager.filter(employee_id=self.intern).update(
            employee_type_id=self.intern_type,
            reporting_manager_id=self.manager,
            contract_end_date=date(2027, 2, 2),
        )
        self.intern = (
            type(self.intern)
            ._base_manager.select_related(
                "employee_work_info__reporting_manager_id",
                "employee_work_info__employee_type_id",
            )
            .get(pk=self.intern.pk)
        )

        self.peer_user = make_user("report-peer")
        self.peer = make_employee(
            company=self.company,
            email="peer@example.test",
            first_name="Synthetic",
            last_name="Peer",
            user=self.peer_user,
            department=self.department,
        )
        self.outsider_user = make_user("report-outsider")
        self.outsider = make_employee(
            company=self.other_company,
            email="outsider@example.test",
            first_name="Other",
            last_name="Tenant",
            user=self.outsider_user,
        )

        self.project = Project._base_manager.create(
            title="Synthetic Internship Project",
            start_date=date(2026, 11, 2),
            end_date=date(2027, 2, 2),
            description="Synthetic project only.",
            company_id=self.company,
        )
        self.task = Task._base_manager.create(
            title="Synthetic milestone",
            project=self.project,
            stage=self.project.project_stages.first(),
            status="completed",
            description="Synthetic task evidence.",
        )
        self.task.task_members.add(self.intern)
        self.policy = ReportingPolicy.all_objects.create(
            company=self.company,
            weekly_due_weekday=4,
            weekly_due_time=time(17, 0),
            weekly_reminder_hours_before=24,
            monthly_due_day=5,
            monthly_reminder_days_before=3,
        )
        self.rubric = ReportingRubricVersion.all_objects.create(
            company=self.company,
            name="Synthetic supervisor rubric",
            version_number=1,
            criteria=[
                {
                    "code": "clarity",
                    "label": "Report clarity",
                    "dimension": "report_writing",
                    "weight": 40,
                    "max_score": 5,
                },
                {
                    "code": "delivery",
                    "label": "Project delivery",
                    "dimension": "internship_performance",
                    "weight": 60,
                    "max_score": 5,
                },
            ],
            approved_by=self.manager,
            approved_at=timezone.now(),
        )

        self.private_media = TemporaryDirectory()
        self.addCleanup(self.private_media.cleanup)
        self.file_fields = [
            MonthlyPresentation._meta.get_field("presentation_file"),
            MonthlyPresentationVersion._meta.get_field("presentation_file"),
        ]
        self.original_storages = [field.storage for field in self.file_fields]
        storage = FileSystemStorage(location=self.private_media.name)
        for field in self.file_fields:
            field.storage = storage
        self.addCleanup(self._restore_storage)
        self.client = Client()

    def _restore_storage(self):
        for field, storage in zip(self.file_fields, self.original_storages):
            field.storage = storage

    def _weekly(self, **overrides):
        values = {
            "employee": self.intern,
            "reporting_week": date(2026, 11, 2),
            "assigned_project": self.project,
            "planned_objectives": "Complete the synthetic milestone.",
            "completed_tasks": "Completed the synthetic milestone.",
            "evidence_links": ["https://example.test/evidence/synthetic"],
            "blockers": "No blockers.",
            "learning_outcomes": "Learned deterministic test design.",
            "next_week_plan": "Prepare the next synthetic milestone.",
            "hours": 35,
            "progress_percent": 75,
        }
        values.update(overrides)
        return save_weekly_draft(**values)

    def _pdf_metadata(self, name="synthetic-presentation.pdf"):
        content = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
        return {
            "file": SimpleUploadedFile(name, content, content_type="application/pdf"),
            "original_filename": name,
            "content_type": "application/pdf",
            "sha256": __import__("hashlib").sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def _monthly(self, **overrides):
        values = {
            "employee": self.intern,
            "presentation_month": date(2026, 11, 1),
            "assigned_project": self.project,
            "presentation_date": date(2026, 11, 30),
            "project_milestones": "Delivered the synthetic project milestone.",
            "file_metadata": self._pdf_metadata(),
        }
        values.update(overrides)
        return save_monthly_draft(**values)

    def _scores(self):
        return {"clarity": 4, "delivery": 3}

    def test_fake_intern_completes_weekly_monthly_and_receives_feedback(self):
        report = self._weekly()
        version = submit_weekly_report(
            employee=self.intern, report=report, task_ids=[self.task.pk]
        )
        weekly_review = review_weekly_report(
            user=self.manager_user,
            version=version,
            outcome=ReviewOutcome.APPROVED,
            comments="Approved synthetic weekly report.",
            task_completion_evidence="Native task and link reviewed.",
            development_feedback="Continue concise evidence notes.",
            writing_quality_comments="Clear report writing.",
            internship_performance_comments="Milestone completed.",
            rubric=self.rubric,
            rubric_scores=self._scores(),
        )
        presentation = self._monthly()
        presentation_version = submit_monthly_presentation(
            employee=self.intern, presentation=presentation
        )
        monthly_review = review_monthly_presentation(
            user=self.manager_user,
            version=presentation_version,
            outcome=ReviewOutcome.APPROVED,
            comments="Approved synthetic presentation.",
            milestone_evidence="Presentation file reviewed.",
            development_feedback="Continue showing milestone evidence.",
            writing_quality_comments="Slides are clear.",
            internship_performance_comments="Project progress is on plan.",
            rubric=self.rubric,
            rubric_scores=self._scores(),
        )
        report.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.APPROVED)
        self.assertEqual(presentation.status, ReportStatus.APPROVED)
        self.assertNotEqual(
            weekly_review.writing_quality_comments,
            weekly_review.internship_performance_comments,
        )
        self.assertNotEqual(
            monthly_review.writing_quality_comments,
            monthly_review.internship_performance_comments,
        )

        self.client.force_login(self.intern_user)
        home = self.client.get(reverse("reporting:home"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Synthetic Internship Project")
        detail = self.client.get(
            reverse("reporting:weekly-detail", kwargs={"public_id": report.public_id})
        )
        self.assertContains(detail, "Approved synthetic weekly report")

    def test_peoplehub_forms_complete_the_synthetic_acceptance_path(self):
        self.client.force_login(self.intern_user)
        weekly_create = self.client.post(
            reverse("reporting:weekly-new"),
            {
                "reporting_week": "2026-11-02",
                "assigned_project": self.project.pk,
                "planned_objectives": "Complete the synthetic interface milestone.",
                "completed_tasks": "Completed the synthetic interface milestone.",
                "completed_task_records": [self.task.pk],
                "evidence_links": "https://example.test/evidence/interface",
                "blockers": "None.",
                "learning_outcomes": "Learned the reporting interface.",
                "next_week_plan": "Continue the synthetic project.",
                "hours": "35.00",
                "progress_percent": "80",
            },
        )
        report = WeeklyReport.all_objects.get(employee=self.intern)
        self.assertRedirects(
            weekly_create,
            reverse("reporting:weekly-detail", kwargs={"public_id": report.public_id}),
        )
        weekly_submit = self.client.post(
            reverse("reporting:weekly-submit", kwargs={"public_id": report.public_id})
        )
        self.assertEqual(weekly_submit.status_code, 302)

        self.client.force_login(self.manager_user)
        weekly_review = self.client.post(
            reverse("reporting:weekly-review", kwargs={"public_id": report.public_id}),
            {
                "outcome": ReviewOutcome.APPROVED,
                "comments": "Approved through the synthetic interface.",
                "evidence_review": "Native task reviewed.",
                "development_feedback": "Continue.",
                "writing_quality_comments": "Clear writing.",
                "internship_performance_comments": "Milestone complete.",
                "rubric": self.rubric.pk,
                "rubric_scores": '{"clarity": 4, "delivery": 4}',
                "reviewer_adjustments": "{}",
                "adjustment_reason": "",
            },
        )
        self.assertEqual(weekly_review.status_code, 302)

        self.client.force_login(self.intern_user)
        presentation_create = self.client.post(
            reverse("reporting:presentation-new"),
            {
                "presentation_month": "2026-11-01",
                "assigned_project": self.project.pk,
                "presentation_date": "2026-11-30",
                "project_milestones": "Synthetic interface milestone delivered.",
                "presentation_file": SimpleUploadedFile(
                    "synthetic-interface.pdf",
                    b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF",
                    content_type="application/pdf",
                ),
            },
        )
        presentation = MonthlyPresentation.all_objects.get(employee=self.intern)
        self.assertRedirects(
            presentation_create,
            reverse(
                "reporting:presentation-detail",
                kwargs={"public_id": presentation.public_id},
            ),
        )
        monthly_submit = self.client.post(
            reverse(
                "reporting:presentation-submit",
                kwargs={"public_id": presentation.public_id},
            )
        )
        self.assertEqual(monthly_submit.status_code, 302)

        self.client.force_login(self.manager_user)
        monthly_review = self.client.post(
            reverse(
                "reporting:presentation-review",
                kwargs={"public_id": presentation.public_id},
            ),
            {
                "outcome": ReviewOutcome.APPROVED,
                "comments": "Presentation approved through the interface.",
                "evidence_review": "File reviewed.",
                "development_feedback": "Continue.",
                "writing_quality_comments": "Clear slides.",
                "internship_performance_comments": "Milestone complete.",
                "rubric": self.rubric.pk,
                "rubric_scores": '{"clarity": 4, "delivery": 4}',
                "reviewer_adjustments": "{}",
                "adjustment_reason": "",
            },
        )
        self.assertEqual(monthly_review.status_code, 302)
        report.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.APPROVED)
        self.assertEqual(presentation.status, ReportStatus.APPROVED)

    def test_reporting_periods_and_configured_deadlines_are_enforced(self):
        with self.assertRaises(ValidationError):
            self._weekly(reporting_week=date(2026, 11, 3))
        with self.assertRaises(ValidationError):
            self._weekly(reporting_week=date(2027, 3, 1))
        with self.assertRaises(ValidationError):
            self._monthly(presentation_month=date(2026, 11, 2))
        report = self._weekly()
        local_deadline = timezone.localtime(report.deadline_at)
        self.assertEqual(local_deadline.date(), date(2026, 11, 6))
        self.assertEqual(local_deadline.time().replace(tzinfo=None), time(17, 0))

    def test_revision_creates_immutable_history_and_requires_current_version(self):
        report = self._weekly()
        version_one = submit_weekly_report(
            employee=self.intern, report=report, task_ids=[self.task.pk]
        )
        review_weekly_report(
            user=self.manager_user,
            version=version_one,
            outcome=ReviewOutcome.REVISION_REQUIRED,
            comments="Add stronger synthetic evidence.",
            rubric=self.rubric,
            rubric_scores=self._scores(),
        )
        report.refresh_from_db()
        report = self._weekly(
            report=report,
            completed_tasks="Completed with expanded synthetic evidence.",
        )
        version_two = submit_weekly_report(
            employee=self.intern, report=report, task_ids=[self.task.pk]
        )
        self.assertEqual(version_two.version_number, 2)
        self.assertEqual(report.versions.count(), 2)
        self.assertEqual(version_one.review.outcome, ReviewOutcome.REVISION_REQUIRED)
        with self.assertRaises(ValidationError):
            version_one.save()
        with self.assertRaises(ValidationError):
            review_weekly_report(
                user=self.manager_user,
                version=version_one,
                outcome=ReviewOutcome.APPROVED,
                comments="Stale approval attempt.",
                rubric=self.rubric,
                rubric_scores=self._scores(),
            )

    def test_review_requires_supervisor_approved_rubric_and_valid_scores(self):
        report = self._weekly()
        version = submit_weekly_report(
            employee=self.intern, report=report, task_ids=[self.task.pk]
        )
        with self.assertRaises(PermissionDenied):
            review_weekly_report(
                user=self.peer_user,
                version=version,
                outcome=ReviewOutcome.APPROVED,
                comments="Unauthorized.",
                rubric=self.rubric,
                rubric_scores=self._scores(),
            )
        unapproved = ReportingRubricVersion.all_objects.create(
            company=self.company,
            name="Unapproved synthetic rubric",
            version_number=1,
            criteria=self.rubric.criteria,
        )
        with self.assertRaises(ValidationError):
            review_weekly_report(
                user=self.manager_user,
                version=version,
                outcome=ReviewOutcome.APPROVED,
                comments="Invalid rubric.",
                rubric=unapproved,
                rubric_scores=self._scores(),
            )
        with self.assertRaises(ValidationError):
            review_weekly_report(
                user=self.manager_user,
                version=version,
                outcome=ReviewOutcome.APPROVED,
                comments="Invalid scores.",
                rubric=self.rubric,
                rubric_scores={"clarity": 9},
            )

    def test_rubric_approval_requires_permission_and_approved_version_is_immutable(
        self,
    ):
        draft = ReportingRubricVersion.all_objects.create(
            company=self.company,
            name="Permission test rubric",
            version_number=1,
            criteria=self.rubric.criteria,
        )
        with self.assertRaises(PermissionDenied):
            approve_rubric(user=self.manager_user, rubric=draft)
        permission = Permission.objects.get(
            content_type__app_label="tvg_reporting",
            codename="approve_reporting_rubric",
        )
        self.manager_user.user_permissions.add(permission)
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )
        approved = approve_rubric(user=self.manager_user, rubric=draft)
        approved.name = "Silently changed rubric"
        with self.assertRaises(ValidationError):
            approved.save()

    def test_access_is_object_level_and_company_scoped(self):
        report = self._weekly()
        self.assertTrue(can_view_item(self.manager_user, self.manager, self.intern))
        self.assertFalse(can_view_item(self.peer_user, self.peer, self.intern))
        self.assertFalse(can_view_item(self.outsider_user, self.outsider, self.intern))
        self.client.force_login(self.peer_user)
        denied = self.client.get(
            reverse("reporting:weekly-detail", kwargs={"public_id": report.public_id})
        )
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.outsider_user)
        cross_tenant = self.client.get(
            reverse("reporting:weekly-detail", kwargs={"public_id": report.public_id})
        )
        self.assertEqual(cross_tenant.status_code, 403)

    def test_private_upload_validation_and_controlled_download(self):
        self.client.force_login(self.intern_user)
        invalid = self.client.post(
            reverse("reporting:presentation-new"),
            {
                "presentation_month": "2026-11-01",
                "assigned_project": self.project.pk,
                "presentation_date": "2026-11-30",
                "project_milestones": "Synthetic",
                "presentation_file": SimpleUploadedFile(
                    "malware.exe",
                    b"not allowed",
                    content_type="application/octet-stream",
                ),
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Upload a PDF or PPTX")
        presentation = self._monthly()
        version = submit_monthly_presentation(
            employee=self.intern, presentation=presentation
        )
        allowed = self.client.get(
            reverse(
                "reporting:presentation-download", kwargs={"version_id": version.pk}
            )
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed["Cache-Control"], "private, no-store")
        with patch("django.http.response.signals.request_finished.send"):
            allowed.close()
        self.client.force_login(self.peer_user)
        denied = self.client.get(
            reverse(
                "reporting:presentation-download", kwargs={"version_id": version.pk}
            )
        )
        self.assertEqual(denied.status_code, 403)

    def test_ai_support_records_versions_and_never_makes_a_decision(self):
        report = self._weekly(evidence_links=[])
        version = submit_weekly_report(
            employee=self.intern, report=report, task_ids=[self.task.pk]
        )
        analysis = request_ai_analysis(
            user=self.manager_user,
            purpose=ReportingAIAnalysis.Purpose.SUPERVISOR_BRIEFING,
            weekly_version=version,
        )
        self.assertEqual(analysis.status, ReportingAIAnalysis.Status.COMPLETED)
        self.assertEqual(analysis.model_version, "synthetic-v1")
        self.assertEqual(analysis.prompt_version, "reporting-v1")
        self.assertIsNone(analysis.result["decision"])
        self.assertIn(
            "Human review is required", analysis.result["supervisor_briefing"]
        )
        with self.assertRaises(ValidationError):
            analysis.save()

    def test_reminders_are_idempotent_and_use_native_notifications(self):
        report = self._weekly()
        reminders = ReportingNotification.all_objects.filter(weekly_report=report)
        self.assertEqual(reminders.count(), 2)
        self._weekly(report=report)
        self.assertEqual(reminders.count(), 2)
        delivered = dispatch_due_notifications(
            as_of=report.deadline_at + timedelta(minutes=2)
        )
        self.assertEqual(len(delivered), 2)
        self.assertTrue(
            all(
                item.status == ReportingNotification.Status.DELIVERED
                for item in delivered
            )
        )
        self.assertEqual(
            dispatch_due_notifications(as_of=report.deadline_at + timedelta(days=1)), []
        )

    def test_management_summary_requires_permission_and_exposes_status_only(self):
        self._weekly()
        with self.assertRaises(PermissionDenied):
            management_summary(user=self.manager_user)
        permission = Permission.objects.get(
            content_type__app_label="tvg_reporting",
            codename="view_management_reporting",
        )
        self.manager_user.user_permissions.add(permission)
        self.manager_user = type(self.manager_user)._base_manager.get(
            pk=self.manager_user.pk
        )
        result = management_summary(user=self.manager_user)
        self.assertEqual(result["interns"], 1)
        self.assertEqual(result["weekly_counts"][0]["status"], ReportStatus.DRAFT)
        self.assertNotIn("completed_tasks", result)
        self.assertTrue(
            ReportingEvent.all_objects.filter(event_type="weekly.draft_saved").exists()
        )
