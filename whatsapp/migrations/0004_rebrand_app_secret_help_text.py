from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("whatsapp", "0003_alter_whatsappcredientials_is_active")]

    operations = [
        migrations.AlterField(
            model_name="whatsappcredientials",
            name="meta_app_secret",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Meta app secret, used to verify the X-Hub-Signature-256 "
                    "header on incoming webhook calls. Without it PeopleOps "
                    "cannot tell a real delivery from a forged one, so unsigned "
                    "payloads are rejected. Find it under App Settings > Basic "
                    "in the Meta developer console."
                ),
                max_length=255,
                verbose_name="App Secret",
            ),
        ),
    ]
