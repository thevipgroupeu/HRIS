from __future__ import annotations

from django.contrib.auth.backends import BaseBackend

from tvg_careers.accounts.models import CandidateAccount, normalize_candidate_email


class CandidateEmailBackend(BaseBackend):
    """Authenticate only verified, active public candidate accounts."""

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        normalized = normalize_candidate_email(email or username)
        if not normalized or password is None:
            return None
        try:
            account = CandidateAccount.objects.get(email=normalized)
        except CandidateAccount.DoesNotExist:
            # Match the password-hash cost of an existing account to reduce
            # observable account-enumeration timing differences.
            dummy = CandidateAccount(email=normalized)
            dummy.set_password(password)
            return None

        password_matches = account.check_password(password)
        if not password_matches:
            return None
        if not account.is_active or not account.email_verified_at:
            return None
        return account

    def get_user(self, user_id):
        try:
            account = CandidateAccount.objects.get(pk=user_id)
        except (CandidateAccount.DoesNotExist, ValueError):
            return None
        if not account.is_active or not account.email_verified_at:
            return None
        return account
