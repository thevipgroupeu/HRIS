from __future__ import annotations

from django.conf import settings
from django.core import signing

from tvg_careers.accounts.models import CandidateAccount

EMAIL_VERIFICATION_SALT = "tvg-careerhub-email-verification-v1"


class InvalidEmailVerificationToken(ValueError):
    pass


def make_email_verification_token(account):
    return signing.dumps(
        {"account_id": str(account.pk), "email": account.email},
        salt=EMAIL_VERIFICATION_SALT,
        compress=True,
    )


def account_from_email_verification_token(token):
    try:
        payload = signing.loads(
            token,
            salt=EMAIL_VERIFICATION_SALT,
            max_age=settings.CAREERHUB_EMAIL_VERIFICATION_TIMEOUT,
        )
        account = CandidateAccount.objects.get(pk=payload["account_id"])
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        CandidateAccount.DoesNotExist,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise InvalidEmailVerificationToken from exc
    if account.email != payload.get("email"):
        raise InvalidEmailVerificationToken
    return account
