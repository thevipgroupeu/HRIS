from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tvg_careers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobopening",
            name="opportunity_type",
            field=models.CharField(
                choices=[("internship", "Internship"), ("job", "Job")],
                db_index=True,
                default="internship",
                max_length=16,
                verbose_name="Opportunity Type",
            ),
        ),
    ]
