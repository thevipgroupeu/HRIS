from django.utils import timezone

from tvg_careers.accounts.models import CandidateAccount

TEST_PASSWORD = "Synthetic-Account-Password-47!"


def make_candidate_account(
    *,
    email="candidate@example.test",
    password=TEST_PASSWORD,
    verified=True,
    **overrides,
):
    values = {
        "first_name": "Synthetic",
        "last_name": "Candidate",
        "privacy_notice_version": "test-notice-v1",
        "privacy_notice_acknowledged_at": timezone.now(),
        "talent_pool_consent_updated_at": timezone.now(),
    }
    if verified:
        values.update(
            {
                "email_verified_at": timezone.now(),
                "identity_status": CandidateAccount.IdentityStatus.LINKED,
                "is_active": True,
            }
        )
    values.update(overrides)
    return CandidateAccount.objects.create_user(
        email=email,
        password=password,
        **values,
    )
