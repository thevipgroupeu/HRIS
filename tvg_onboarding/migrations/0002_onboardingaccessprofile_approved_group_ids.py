from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tvg_onboarding", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboardingaccessprofile",
            name="approved_group_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
