from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tvg_careerhub_accounts", "0005_alter_candidatesecurityevent_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="candidatesecurityevent",
            name="event_type",
            field=models.CharField(
                max_length=32,
                db_index=True,
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
                    ("verification_uploaded", "Verification uploaded"),
                    ("interview_booked", "Interview booked"),
                    ("interview_rescheduled", "Interview rescheduled"),
                    ("arrangement_dates_confirmed", "Arrangement dates confirmed"),
                    ("acceptance_downloaded", "Acceptance downloaded"),
                    ("acceptance_received", "Acceptance receipt confirmed"),
                ],
            ),
        ),
    ]
