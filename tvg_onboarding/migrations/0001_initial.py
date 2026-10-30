import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import tvg_onboarding.models
import tvg_onboarding.storage


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("asset", "0012_alter_asset_is_active_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("base", "0020_alter_company_address_alter_company_city_and_more"),
        ("employee", "0009_alter_actiontype_is_active_and_more"),
        ("onboarding", "0003_alter_candidatestage_is_active_and_more"),
        ("tvg_core", "0001_initial"),
        ("tvg_erasmus", "0003_erasmuscase_before_mobility_completed_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetAcknowledgmentTemplate",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("name", models.CharField(max_length=120)),
                ("version_number", models.PositiveIntegerField(default=1)),
                ("body", models.TextField()),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="base.company"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
            ],
            options={
                "permissions": (
                    (
                        "approve_asset_ack_template",
                        "Approve asset acknowledgment templates",
                    ),
                ),
            },
        ),
        migrations.CreateModel(
            name="InternOnboarding",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_approval", "Pending approval"),
                            ("approved", "Approved"),
                            ("in_progress", "In progress"),
                            ("onboarded", "Onboarded"),
                        ],
                        default="pending_approval",
                        max_length=24,
                    ),
                ),
                ("intern_id", models.CharField(max_length=64, unique=True)),
                ("employee_badge_id", models.CharField(max_length=64, unique=True)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("onboarded_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="approved_tvg_onboardings",
                        to="employee.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="base.department",
                    ),
                ),
                (
                    "employee",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_intern_onboarding",
                        to="employee.employee",
                    ),
                ),
                (
                    "erasmus_case",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="intern_onboarding",
                        to="tvg_erasmus.erasmuscase",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "native_candidate_stage",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_intern_onboarding",
                        to="onboarding.candidatestage",
                    ),
                ),
                (
                    "native_stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_intern_onboardings",
                        to="onboarding.onboardingstage",
                    ),
                ),
                (
                    "onboarded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="completed_tvg_onboardings",
                        to="employee.employee",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="onboardings",
                        to="tvg_core.person",
                    ),
                ),
                (
                    "supervisor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supervised_tvg_onboardings",
                        to="employee.employee",
                    ),
                ),
                (
                    "venture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="tvg_core.venture",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "permissions": (
                    ("start_intern_onboarding", "Start intern onboarding"),
                    ("approve_intern_onboarding", "Approve intern onboarding"),
                    ("manage_intern_onboarding", "Manage intern onboarding"),
                    ("complete_intern_onboarding", "Complete intern onboarding"),
                    ("manage_onboarding_access", "Manage onboarding access"),
                    ("manage_account_provisioning", "Manage account provisioning"),
                    ("allocate_onboarding_asset", "Allocate onboarding assets"),
                ),
            },
        ),
        migrations.CreateModel(
            name="AccountProvisioningRequest",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("provider", models.CharField(max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("requested", "Requested"),
                            ("manual_required", "Manual action required"),
                            ("provisioned", "Provisioned"),
                            ("failed", "Failed"),
                        ],
                        default="requested",
                        max_length=24,
                    ),
                ),
                ("requested_email", models.EmailField(blank=True, max_length=254)),
                ("provisioned_email", models.EmailField(blank=True, max_length=254)),
                ("external_reference", models.CharField(blank=True, max_length=180)),
                (
                    "idempotency_key",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "requested_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="completed_tvg_accounts",
                        to="employee.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requested_tvg_accounts",
                        to="employee.employee",
                    ),
                ),
                (
                    "onboarding",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="provisioning_request",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="ManualITTask",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("title", models.CharField(max_length=180)),
                ("instructions", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="open",
                        max_length=16,
                    ),
                ),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "provisioning_request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="manual_task",
                        to="tvg_onboarding.accountprovisioningrequest",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="OnboardingAccessProfile",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="base.company"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="base.department",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        related_name="tvg_onboarding_access_profiles", to="auth.group"
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "venture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="tvg_core.venture",
                    ),
                ),
            ],
            options={
                "permissions": (
                    (
                        "approve_onboarding_access_profile",
                        "Approve onboarding access profiles",
                    ),
                ),
            },
        ),
        migrations.CreateModel(
            name="OnboardingAccessGrant",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("group_snapshot", models.JSONField(default=list)),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "onboarding",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="access_grant",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="tvg_onboarding.onboardingaccessprofile",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="OnboardingAssetAllocation",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("document_version", models.PositiveIntegerField(default=0)),
                ("rendered_text", models.TextField(blank=True)),
                (
                    "pdf_file",
                    models.FileField(
                        blank=True,
                        max_length=500,
                        storage=tvg_onboarding.storage.OnboardingPrivateStorage(),
                        upload_to=tvg_onboarding.models.acknowledgment_path,
                    ),
                ),
                ("sha256", models.CharField(blank=True, max_length=64)),
                (
                    "acknowledgment_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("prepared", "Prepared"),
                            ("acknowledged", "Acknowledged"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("prepared_at", models.DateTimeField(blank=True, null=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                (
                    "acknowledgment_reference",
                    models.CharField(blank=True, max_length=180),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "native_assignment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_onboarding_allocation",
                        to="asset.assetassignment",
                    ),
                ),
                (
                    "onboarding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="asset_allocations",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
                (
                    "prepared_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="prepared_tvg_asset_acknowledgments",
                        to="employee.employee",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="tvg_onboarding.assetacknowledgmenttemplate",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="OnboardingChecklistLink",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("code", models.CharField(max_length=64)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("required", models.BooleanField(default=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("evidence_note", models.CharField(blank=True, max_length=255)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "native_task",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_checklist_link",
                        to="onboarding.candidatetask",
                    ),
                ),
                (
                    "onboarding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="checklist_items",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="OnboardingEvent",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("event_type", models.CharField(max_length=80)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="employee.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "onboarding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="events",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
            ],
            options={
                "ordering": ("occurred_at",),
            },
        ),
        migrations.CreateModel(
            name="OnboardingPolicy",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "intern_id_prefix",
                    models.CharField(default="TVG-INT", max_length=20),
                ),
                (
                    "employee_id_prefix",
                    models.CharField(default="TVG-EMP", max_length=20),
                ),
                (
                    "welcome_email_lead_days",
                    models.PositiveSmallIntegerField(default=7),
                ),
                (
                    "identity_provider",
                    models.CharField(
                        choices=[
                            ("manual", "Manual IT task"),
                            ("zoho", "Zoho"),
                            ("self_hosted", "Self-hosted identity/mail"),
                        ],
                        default="manual",
                        max_length=24,
                    ),
                ),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_onboarding_policy",
                        to="base.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
            ],
            options={
                "permissions": (
                    ("configure_onboarding_policy", "Configure onboarding policy"),
                ),
            },
        ),
        migrations.CreateModel(
            name="ScheduledOnboardingCommunication",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("welcome", "Welcome email"),
                            ("arrival", "Arrival reminder and office instructions"),
                        ],
                        max_length=16,
                    ),
                ),
                ("scheduled_for", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("scheduled", "Scheduled"),
                            ("sent", "Sent"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="scheduled",
                        max_length=16,
                    ),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("external_reference", models.CharField(blank=True, max_length=180)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "onboarding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="communications",
                        to="tvg_onboarding.internonboarding",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="StaffIdSequence",
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
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, null=True, verbose_name="Created At"
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("year", models.PositiveSmallIntegerField()),
                (
                    "kind",
                    models.CharField(
                        choices=[("intern", "Intern"), ("employee", "Employee")],
                        max_length=16,
                    ),
                ),
                ("next_number", models.PositiveIntegerField(default=1)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="base.company"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="assetacknowledgmenttemplate",
            constraint=models.UniqueConstraint(
                fields=("company", "name", "version_number"),
                name="tvg_onboarding_unique_asset_template_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="onboardingaccessprofile",
            constraint=models.UniqueConstraint(
                fields=("company", "name"), name="tvg_onboarding_unique_access_profile"
            ),
        ),
        migrations.AddConstraint(
            model_name="onboardingchecklistlink",
            constraint=models.UniqueConstraint(
                fields=("onboarding", "code"),
                name="tvg_onboarding_unique_checklist_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduledonboardingcommunication",
            constraint=models.UniqueConstraint(
                fields=("onboarding", "kind"),
                name="tvg_onboarding_unique_scheduled_communication",
            ),
        ),
        migrations.AddConstraint(
            model_name="staffidsequence",
            constraint=models.UniqueConstraint(
                fields=("company", "year", "kind"),
                name="tvg_onboarding_unique_staff_id_sequence",
            ),
        ),
    ]
