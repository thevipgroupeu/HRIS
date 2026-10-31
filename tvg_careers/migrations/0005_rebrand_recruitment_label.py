import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tvg_careers", "0004_application_questionnaire_snapshot_and_more")
    ]

    operations = [
        migrations.AlterField(
            model_name="jobopening",
            name="recruitment",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tvg_job_opening",
                to="recruitment.recruitment",
                verbose_name="Recruitment Record",
            ),
        ),
    ]
