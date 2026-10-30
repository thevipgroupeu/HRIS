import hashlib
import json
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_questionnaire_versions(apps, schema_editor):
    JobOpening = apps.get_model("tvg_careers", "JobOpening")
    QuestionnaireVersion = apps.get_model(
        "tvg_careers", "RecruitmentQuestionnaireVersion"
    )
    RecruitmentSurvey = apps.get_model("recruitment", "RecruitmentSurvey")

    recruitment_ids = (
        JobOpening.objects.order_by()
        .values_list("recruitment_id", flat=True)
        .distinct()
    )
    for recruitment_id in recruitment_ids.iterator():
        questions = (
            RecruitmentSurvey.objects.filter(
                template_id__recruitment__id=recruitment_id
            )
            .distinct()
            .order_by("sequence", "pk")
        )
        definition = []
        for question in questions:
            options = []
            if question.type in {"options", "multiple"}:
                options = [
                    option.strip()
                    for option in str(question.options or "").split(",")
                    if option.strip()
                ]
            definition.append(
                {
                    "native_question_id": question.pk,
                    "public_id": str(uuid.uuid4()),
                    "label": str(question.question).strip(),
                    "type": question.type,
                    "required": bool(question.is_mandatory),
                    "options": options,
                    "sequence": question.sequence or 0,
                }
            )
        fingerprint_input = [
            {key: value for key, value in question.items() if key != "public_id"}
            for question in definition
        ]
        canonical = json.dumps(
            fingerprint_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        QuestionnaireVersion.objects.get_or_create(
            recruitment_id=recruitment_id,
            version_number=1,
            defaults={
                "public_id": uuid.uuid4(),
                "fingerprint": hashlib.sha256(canonical).hexdigest(),
                "definition_snapshot": definition,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("recruitment", "0011_remove_recruitmentsurvey_recruitment_ids_and_more"),
        ("tvg_careers", "0003_application_withdrawn_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="application",
            name="questionnaire_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="application",
            name="source",
            field=models.CharField(
                choices=[
                    ("company_website", "Company website"),
                    ("linkedin", "LinkedIn"),
                    ("erasmusintern", "ErasmusIntern"),
                    ("job_board", "Job board"),
                    ("university_referral", "University referral"),
                    ("referral", "Referral"),
                ],
                default="company_website",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="utm_parameters",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="ApplicationConfirmationEvent",
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
                    "event_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "event_type",
                    models.CharField(
                        default="career.application_submitted.v1",
                        editable=False,
                        max_length=80,
                    ),
                ),
                (
                    "schema_version",
                    models.PositiveSmallIntegerField(default=1, editable=False),
                ),
                ("payload", models.JSONField(default=dict)),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("delivery_attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                (
                    "application",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="confirmation_event",
                        to="tvg_careers.application",
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
                "verbose_name": "Application Confirmation Event",
                "verbose_name_plural": "Application Confirmation Events",
                "ordering": ("occurred_at",),
            },
        ),
        migrations.CreateModel(
            name="RecruitmentQuestionnaireVersion",
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
                ("version_number", models.PositiveIntegerField()),
                ("fingerprint", models.CharField(max_length=64)),
                ("definition_snapshot", models.JSONField(default=list)),
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
                    "recruitment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tvg_questionnaire_versions",
                        to="recruitment.recruitment",
                    ),
                ),
            ],
            options={
                "verbose_name": "CareerHub Questionnaire Version",
                "verbose_name_plural": "CareerHub Questionnaire Versions",
                "ordering": ("recruitment_id", "-version_number"),
            },
        ),
        migrations.AddField(
            model_name="application",
            name="questionnaire_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="applications",
                to="tvg_careers.recruitmentquestionnaireversion",
            ),
        ),
        migrations.AddConstraint(
            model_name="recruitmentquestionnaireversion",
            constraint=models.UniqueConstraint(
                fields=("recruitment", "version_number"),
                name="tvg_careers_unique_questionnaire_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="recruitmentquestionnaireversion",
            constraint=models.UniqueConstraint(
                fields=("recruitment", "fingerprint"),
                name="tvg_careers_unique_questionnaire_fingerprint",
            ),
        ),
        migrations.RunPython(
            backfill_questionnaire_versions,
            migrations.RunPython.noop,
        ),
    ]
