import uuid

import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import tvg_careers.accounts.models


class Migration(migrations.Migration):
    dependencies = [
        ("tvg_careerhub_accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="candidatesecurityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("account_created", "Account created"),
                    ("verification_sent", "Verification email sent"),
                    ("email_verified", "Email verified"),
                    ("identity_review", "Identity review required"),
                    ("login_succeeded", "Login succeeded"),
                    ("login_failed", "Login failed"),
                    ("logout", "Logout"),
                    ("recovery_requested", "Recovery requested"),
                    ("password_reset", "Password reset"),
                    ("profile_updated", "Profile updated"),
                    ("document_uploaded", "Document uploaded"),
                    ("application_submitted", "Application submitted"),
                    ("application_withdrawn", "Application withdrawn"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="CandidateProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("phone_number", models.CharField(blank=True, max_length=32)),
                ("address_line", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=80)),
                ("postal_code", models.CharField(blank=True, max_length=24)),
                ("country_of_residence", models.CharField(blank=True, max_length=80)),
                ("university", models.CharField(blank=True, max_length=180)),
                ("degree_programme", models.CharField(blank=True, max_length=180)),
                ("field_of_study", models.CharField(blank=True, max_length=180)),
                ("expected_graduation", models.DateField(blank=True, null=True)),
                ("languages", models.JSONField(blank=True, default=list)),
                ("linkedin_url", models.URLField(blank=True, max_length=300)),
                ("github_url", models.URLField(blank=True, max_length=300)),
                ("portfolio_url", models.URLField(blank=True, max_length=300)),
                ("preferred_start_date", models.DateField(blank=True, null=True)),
                ("preferred_end_date", models.DateField(blank=True, null=True)),
                (
                    "preferred_duration_weeks",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "erasmus_declaration",
                    models.CharField(
                        choices=[
                            ("eligible_funded", "Eligible and funding confirmed"),
                            ("eligible_planning", "Eligible; funding in progress"),
                            ("eligible_unfunded", "Eligible without confirmed funding"),
                            ("not_eligible", "Not eligible"),
                            ("unsure", "Unsure"),
                            ("not_declared", "Prefer not to declare"),
                        ],
                        default="not_declared",
                        max_length=24,
                    ),
                ),
                (
                    "erasmus_declaration_updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="candidate_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"verbose_name": "CareerHub candidate profile"},
        ),
        migrations.CreateModel(
            name="CandidateProfileDocument",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("cv", "CV"), ("cover_letter", "Cover letter")],
                        max_length=20,
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        max_length=300,
                        upload_to=tvg_careers.accounts.models._private_document_path,
                    ),
                ),
                ("original_name", models.CharField(max_length=255)),
                (
                    "content_type",
                    models.CharField(default="application/pdf", max_length=100),
                ),
                ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("is_current", models.BooleanField(db_index=True, default=True)),
                (
                    "uploaded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("kind", "-uploaded_at")},
        ),
        migrations.CreateModel(
            name="CandidateApplication",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("opening_public_id", models.UUIDField()),
                ("opening_slug", models.SlugField(max_length=160)),
                ("opening_title", models.CharField(max_length=200)),
                ("venture_slug", models.SlugField(max_length=80)),
                ("venture_name", models.CharField(max_length=80)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("submitted", "Submitted"),
                            ("withdrawn", "Withdrawn"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("profile_snapshot", models.JSONField(blank=True, default=dict)),
                ("questionnaire_snapshot", models.JSONField(blank=True, default=list)),
                (
                    "cv_snapshot",
                    models.FileField(
                        blank=True,
                        max_length=300,
                        upload_to=tvg_careers.accounts.models._application_snapshot_path,
                    ),
                ),
                ("cv_original_name", models.CharField(blank=True, max_length=255)),
                ("cv_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "cover_letter_snapshot",
                    models.FileField(
                        blank=True,
                        max_length=300,
                        upload_to=tvg_careers.accounts.models._application_snapshot_path,
                    ),
                ),
                (
                    "cover_letter_original_name",
                    models.CharField(blank=True, max_length=255),
                ),
                ("cover_letter_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "friendly_status_code",
                    models.CharField(default="draft", max_length=32),
                ),
                (
                    "friendly_status_label",
                    models.CharField(default="Draft", max_length=80),
                ),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("withdrawn_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status_updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="candidate_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="CandidateApplicationStatusEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.CharField(max_length=32)),
                ("label", models.CharField(max_length=80)),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="status_events",
                        to="tvg_careerhub_accounts.candidateapplication",
                    ),
                ),
            ],
            options={"ordering": ("-occurred_at", "-pk")},
        ),
        migrations.AddConstraint(
            model_name="candidateprofiledocument",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("account", "kind"),
                name="tvg_careerhub_one_current_document_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="candidateapplication",
            constraint=models.UniqueConstraint(
                fields=("account", "opening_public_id"),
                name="tvg_careerhub_one_application_per_opening",
            ),
        ),
        migrations.AddConstraint(
            model_name="candidateapplicationstatusevent",
            constraint=models.UniqueConstraint(
                fields=("application", "code", "occurred_at"),
                name="tvg_careerhub_unique_application_status_event",
            ),
        ),
    ]
