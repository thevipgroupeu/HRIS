from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security)
def automation_configuration_check(app_configs, **kwargs):
    if not getattr(settings, "TVG_AUTOMATION_ENABLED", False):
        return []
    findings = []
    environment = getattr(settings, "TVG_AUTOMATION_ENVIRONMENT", "development")
    url = getattr(settings, "TVG_ACTIVEPIECES_WEBHOOK_URL", "")
    parsed = urlparse(url)
    if not url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        findings.append(
            Error(
                "Enabled automation requires a valid Activepieces webhook URL.",
                id="tvg_automation.E001",
            )
        )
    for setting_name, check_id in (
        ("TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET", "tvg_automation.E002"),
        ("TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET", "tvg_automation.E003"),
    ):
        value = getattr(settings, setting_name, "")
        if len(value) < 32 or "change-me" in value.casefold():
            findings.append(
                Error(
                    f"{setting_name} must be a non-placeholder independent secret "
                    "of at least 32 characters.",
                    id=check_id,
                )
            )
    outbound_secret = getattr(settings, "TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET", "")
    callback_secret = getattr(settings, "TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET", "")
    if outbound_secret and outbound_secret == callback_secret:
        findings.append(
            Error(
                "Outbound and callback HMAC secrets must be different.",
                id="tvg_automation.E008",
            )
        )
    if environment in {"production", "staging"} and parsed.scheme != "https":
        findings.append(
            Error(
                "Staging and production automation webhooks must use HTTPS.",
                id="tvg_automation.E004",
            )
        )
    if environment in {"production", "staging"} and (
        not parsed.hostname or parsed.hostname.endswith(".invalid")
    ):
        findings.append(
            Error(
                "Staging and production require a real Activepieces hostname.",
                id="tvg_automation.E009",
            )
        )
    if parsed.username or parsed.password:
        findings.append(
            Error(
                "Do not embed credentials in the Activepieces webhook URL.",
                id="tvg_automation.E010",
            )
        )
    if getattr(settings, "TVG_RECRUITMENT_FROM_EMAIL", "").casefold() != (
        "recruitment@thevipgroup.eu"
    ):
        findings.append(
            Error(
                "Recruitment automation sender must be recruitment@thevipgroup.eu.",
                id="tvg_automation.E005",
            )
        )
    backend = getattr(settings, "TVG_AUTOMATION_TRANSPORT_BACKEND", "")
    if "DisabledActivepiecesTransport" in backend:
        findings.append(
            Error(
                "Enabled automation cannot use the disabled transport backend.",
                id="tvg_automation.E007",
            )
        )
    if environment in {"production", "staging"} and "Synthetic" in backend:
        findings.append(
            Error(
                "Synthetic automation transport is forbidden in staging/production.",
                id="tvg_automation.E006",
            )
        )
    if environment not in {"development", "test", "staging", "production"}:
        findings.append(
            Warning(
                "TVG_AUTOMATION_ENVIRONMENT has a non-standard value.",
                id="tvg_automation.W001",
            )
        )
    return findings
