from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from tvg_careers.accounts.forms import (
    CandidateLoginForm,
    CandidatePasswordResetForm,
    CandidateProfileForm,
    CandidateSetPasswordForm,
    CandidateSignupForm,
)
from tvg_careers.accounts.models import (
    CandidateAccount,
    CandidateProfile,
    CandidateSecurityEvent,
    normalize_candidate_email,
)
from tvg_careers.accounts.services import (
    IdentityReviewRequired,
    IdentityServiceUnavailable,
    PeopleOpsIdentityClient,
    log_security_event,
    send_verification_email_safely,
)
from tvg_careers.accounts.throttling import client_ip, consume_rate, reset_rate
from tvg_careers.accounts.tokens import (
    InvalidEmailVerificationToken,
    account_from_email_verification_token,
)
from tvg_careers.public_views import _base_context


def _account_context(request, *, title, description, path=None):
    context = _base_context(
        request,
        title=title,
        description=description,
        path=path,
    )
    context.update(
        {
            "robots": "noindex, nofollow",
            "privacy_notice_version": settings.CAREERHUB_PRIVACY_NOTICE_VERSION,
        }
    )
    return context


def _safe_next(request):
    value = request.POST.get("next") or request.GET.get("next") or ""
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return ""


def _rate_limited(request, *, retry_after):
    context = _account_context(
        request,
        title="Please wait before trying again",
        description="CareerHub temporarily limited this authentication request.",
    )
    response = render(
        request,
        "careerhub/accounts/rate_limited.html",
        context,
        status=429,
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("careerhub:candidate-profile")
    next_url = _safe_next(request)
    context = _account_context(
        request,
        title="Create your candidate account",
        description="Create a secure THEVIPGROUP CareerHub candidate account.",
    )
    if request.method == "GET":
        context.update({"form": CandidateSignupForm(), "next": next_url})
        return render(request, "careerhub/accounts/signup.html", context)

    ip_result = consume_rate("signup_ip", client_ip(request))
    if not ip_result.allowed:
        return _rate_limited(request, retry_after=ip_result.retry_after)

    form = CandidateSignupForm(request.POST)
    context.update({"form": form, "next": next_url})
    if not form.is_valid():
        return render(request, "careerhub/accounts/signup.html", context, status=400)
    if next_url:
        request.session["candidate_post_verify_next"] = next_url

    email = form.cleaned_data["email"]
    email_result = consume_rate("signup_email", email)
    if not email_result.allowed:
        return _rate_limited(request, retry_after=email_result.retry_after)

    created = False
    try:
        account = CandidateAccount.objects.get(email=email)
    except CandidateAccount.DoesNotExist:
        try:
            with transaction.atomic():
                account = form.create_account()
                created = True
        except IntegrityError:
            account = CandidateAccount.objects.get(email=email)

    if created:
        log_security_event(
            CandidateSecurityEvent.EventType.ACCOUNT_CREATED,
            request=request,
            account=account,
        )
    if not account.email_verified_at:
        send_verification_email_safely(account, request=request)

    context = _account_context(
        request,
        title="Check your email",
        description="Candidate account verification instructions.",
    )
    return render(request, "careerhub/accounts/signup_complete.html", context)


@require_http_methods(["GET", "POST"])
def verify_email(request, token):
    context = _account_context(
        request,
        title="Verify your email",
        description="Confirm ownership of your CareerHub account email.",
    )
    try:
        account = account_from_email_verification_token(token)
    except InvalidEmailVerificationToken:
        return render(
            request,
            "careerhub/accounts/verification_invalid.html",
            context,
            status=400,
        )

    if account.email_verified_at:
        return redirect(f"{reverse('careerhub:candidate-login')}?verified=1")
    if request.method == "GET":
        return render(request, "careerhub/accounts/verify_email.html", context)

    rate = consume_rate("verification_ip", client_ip(request))
    if not rate.allowed:
        return _rate_limited(request, retry_after=rate.retry_after)

    identity_status = CandidateAccount.IdentityStatus.LINKED
    try:
        PeopleOpsIdentityClient().provision(account)
    except IdentityReviewRequired:
        identity_status = CandidateAccount.IdentityStatus.REVIEW_REQUIRED
    except IdentityServiceUnavailable:
        return render(
            request,
            "careerhub/accounts/verification_unavailable.html",
            context,
            status=503,
        )

    with transaction.atomic():
        account = CandidateAccount.objects.select_for_update(of=("self",)).get(pk=account.pk)
        if not account.email_verified_at:
            account.email_verified_at = timezone.now()
            account.identity_status = identity_status
            account.is_active = True
            account.save(
                update_fields=(
                    "email_verified_at",
                    "identity_status",
                    "is_active",
                    "updated_at",
                )
            )
    event = (
        CandidateSecurityEvent.EventType.IDENTITY_REVIEW
        if identity_status == CandidateAccount.IdentityStatus.REVIEW_REQUIRED
        else CandidateSecurityEvent.EventType.EMAIL_VERIFIED
    )
    log_security_event(event, request=request, account=account)
    next_url = request.session.pop("candidate_post_verify_next", "")
    query = "verified=1"
    if next_url:
        query = urlencode({"verified": "1", "next": next_url})
    return redirect(f"{reverse('careerhub:candidate-login')}?{query}")


@require_http_methods(["GET", "POST"])
def candidate_login(request):
    if request.user.is_authenticated:
        return redirect("careerhub:candidate-profile")
    next_url = _safe_next(request)
    context = _account_context(
        request,
        title="Candidate sign in",
        description="Secure sign in for THEVIPGROUP CareerHub candidates.",
    )
    context.update(
        {
            "form": CandidateLoginForm(request.POST or None),
            "next": next_url,
            "verification_complete": request.GET.get("verified") == "1",
        }
    )
    if request.method == "GET":
        return render(request, "careerhub/accounts/login.html", context)

    ip = client_ip(request)
    ip_result = consume_rate("login_ip", ip)
    if not ip_result.allowed:
        return _rate_limited(request, retry_after=ip_result.retry_after)
    form = context["form"]
    if not form.is_valid():
        return render(request, "careerhub/accounts/login.html", context, status=400)

    email = form.cleaned_data["email"]
    pair_key = f"{ip}:{email}"
    pair_result = consume_rate("login_pair", pair_key)
    if not pair_result.allowed:
        return _rate_limited(request, retry_after=pair_result.retry_after)

    account = authenticate(
        request,
        email=email,
        password=form.cleaned_data["password"],
    )
    if account is None:
        known_account = CandidateAccount.objects.filter(email=email).first()
        log_security_event(
            CandidateSecurityEvent.EventType.LOGIN_FAILED,
            request=request,
            account=known_account,
        )
        context["authentication_error"] = (
            "The email or password is incorrect, or the account is not verified."
        )
        return render(request, "careerhub/accounts/login.html", context, status=400)

    login(request, account)
    reset_rate("login_pair", pair_key)
    log_security_event(
        CandidateSecurityEvent.EventType.LOGIN_SUCCEEDED,
        request=request,
        account=account,
    )
    return redirect(next_url or "careerhub:candidate-profile")


@require_POST
@login_required
def candidate_logout(request):
    account = request.user
    log_security_event(
        CandidateSecurityEvent.EventType.LOGOUT,
        request=request,
        account=account,
    )
    logout(request)
    return redirect(f"{reverse('careerhub:candidate-login')}?signed_out=1")


@login_required
@require_http_methods(["GET", "POST"])
def candidate_profile(request):
    account = request.user
    profile, _ = CandidateProfile.objects.get_or_create(account=account)
    context = _account_context(
        request,
        title="Your candidate profile",
        description="Manage your THEVIPGROUP CareerHub candidate account.",
    )
    form = CandidateProfileForm(
        request.POST or None,
        instance=account,
        profile=profile,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        log_security_event(
            CandidateSecurityEvent.EventType.PROFILE_UPDATED,
            request=request,
            account=account,
            metadata={"consent_changed": "talent_pool_consent" in form.changed_data},
        )
        return redirect(f"{reverse('careerhub:candidate-profile')}?saved=1")
    context.update(
        {
            "account": account,
            "form": form,
            "profile_saved": request.GET.get("saved") == "1",
        }
    )
    status = 400 if request.method == "POST" and form.errors else 200
    return render(request, "careerhub/accounts/profile.html", context, status=status)


class CandidatePageContextMixin:
    page_title = "Candidate account"
    page_description = "Secure THEVIPGROUP CareerHub candidate account."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            _account_context(
                self.request,
                title=self.page_title,
                description=self.page_description,
            )
        )
        return context


class CandidatePasswordResetView(CandidatePageContextMixin, PasswordResetView):
    template_name = "careerhub/accounts/password_reset.html"
    form_class = CandidatePasswordResetForm
    email_template_name = "careerhub/emails/password_reset.txt"
    html_email_template_name = "careerhub/emails/password_reset.html"
    subject_template_name = "careerhub/emails/password_reset_subject.txt"
    success_url = reverse_lazy("careerhub:candidate-password-reset-done")
    page_title = "Reset your password"

    def post(self, request, *args, **kwargs):
        email = normalize_candidate_email(request.POST.get("email"))
        ip_result = consume_rate("recovery_ip", client_ip(request))
        email_result = consume_rate("recovery_email", email or "missing")
        if not ip_result.allowed or not email_result.allowed:
            return _rate_limited(
                request,
                retry_after=max(ip_result.retry_after, email_result.retry_after),
            )
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        account = CandidateAccount.objects.filter(
            email=form.cleaned_data["email"],
            is_active=True,
            email_verified_at__isnull=False,
        ).first()
        if account:
            log_security_event(
                CandidateSecurityEvent.EventType.RECOVERY_REQUESTED,
                request=self.request,
                account=account,
            )
        return super().form_valid(form)


class CandidatePasswordResetDoneView(CandidatePageContextMixin, PasswordResetDoneView):
    template_name = "careerhub/accounts/password_reset_done.html"
    page_title = "Check your email"


class CandidatePasswordResetConfirmView(
    CandidatePageContextMixin, PasswordResetConfirmView
):
    template_name = "careerhub/accounts/password_reset_confirm.html"
    form_class = CandidateSetPasswordForm
    success_url = reverse_lazy("careerhub:candidate-password-reset-complete")
    page_title = "Choose a new password"

    def form_valid(self, form):
        account = self.user
        response = super().form_valid(form)
        log_security_event(
            CandidateSecurityEvent.EventType.PASSWORD_RESET,
            request=self.request,
            account=account,
        )
        return response


class CandidatePasswordResetCompleteView(
    CandidatePageContextMixin, PasswordResetCompleteView
):
    template_name = "careerhub/accounts/password_reset_complete.html"
    page_title = "Password updated"
