from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tvg_careerhub_accounts", "0002_profile_documents_and_applications"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidateapplication",
            name="cover_letter_content_type",
            field=models.CharField(default="application/pdf", max_length=100),
        ),
        migrations.AddField(
            model_name="candidateapplication",
            name="cv_content_type",
            field=models.CharField(default="application/pdf", max_length=100),
        ),
        migrations.AddField(
            model_name="candidateapplication",
            name="questionnaire_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="candidateapplication",
            name="questionnaire_version_number",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="candidateapplication",
            name="questionnaire_version_public_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="candidateapplication",
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
            model_name="candidateapplication",
            name="utm_parameters",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
