from __future__ import annotations

import logging
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from tvg_careers.accounts.models import CandidateSecurityEvent
from tvg_careers.accounts.throttling import client_ip, fingerprint
from tvg_careers.accounts.tokens import make_email_verification_token

logger = logging.getLogger(__name__)


class IdentityServiceUnavailable(RuntimeError):
    pass


class IdentityReviewRequired(RuntimeError):
    pass


class PeopleOpsIdentityClient:
    def __init__(self, *, session=None):
        self.session = session or requests.Session()
        self.base_url = settings.CAREERHUB_PEOPLEOPS_API_URL.rstrip("/")
        self.token = settings.CAREERHUB_IDENTITY_API_TOKEN
        self.timeout = settings.CAREERHUB_API_TIMEOUT

    def provision(self, account):
        try:
            response = self.session.post(
                f"{self.base_url}/identities/",
                json={
                    "account_id": str(account.pk),
                    "person_public_id": str(account.person_public_id),
                    "email": account.email,
                    "preferred_name": account.get_full_name(),
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "THEVIPGROUP-CareerHub/1.0",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise IdentityServiceUnavailable from exc
        if response.status_code == 409:
            raise IdentityReviewRequired
        if response.status_code != 200:
            raise IdentityServiceUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise IdentityServiceUnavailable from exc
        if payload.get("person_public_id") != str(account.person_public_id):
            raise IdentityServiceUnavailable
        return payload


def log_security_event(event_type, *, request, account=None, metadata=None):
    CandidateSecurityEvent.objects.create(
        account=account,
        event_type=event_type,
        ip_fingerprint=fingerprint(client_ip(request), namespace="ip"),
        user_agent_fingerprint=fingerprint(
            request.META.get("HTTP_USER_AGENT", ""),
            namespace="user-agent",
        ),
        metadata=metadata or {},
    )


def send_verification_email(account, *, request):
    token = make_email_verification_token(account)
    path = reverse("careerhub:candidate-verify", kwargs={"token": token})
    base_url = settings.CAREERHUB_PUBLIC_BASE_URL.rstrip("/")
    verification_url = urljoin(f"{base_url}/", path.lstrip("/"))
    context = {
        "account": account,
        "verification_url": verification_url,
        "timeout_hours": settings.CAREERHUB_EMAIL_VERIFICATION_TIMEOUT // 3600,
        "contact_email": settings.CAREERHUB_CONTACT_EMAIL,
    }
    subject = render_to_string(
        "careerhub/emails/verify_email_subject.txt", context
    ).strip()
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string("careerhub/emails/verify_email.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[account.email],
    )
    message.attach_alternative(
        render_to_string("careerhub/emails/verify_email.html", context),
        "text/html",
    )
    message.send(fail_silently=False)
    log_security_event(
        CandidateSecurityEvent.EventType.VERIFICATION_SENT,
        request=request,
        account=account,
    )


def send_verification_email_safely(account, *, request):
    try:
        send_verification_email(account, request=request)
    except Exception:
        # SMTP exceptions can include the recipient. Keep logs intentionally
        # data-free; a repeated generic signup request retries delivery.
        logger.error("Candidate verification email delivery failed")
        return False
    return True
