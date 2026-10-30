from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tvg_careers", "0002_jobopening_opportunity_type")]

    operations = [
        migrations.AddField(
            model_name="application",
            name="withdrawn_at",
            field=models.DateTimeField(blank=True, null=True),
        )
    ]
