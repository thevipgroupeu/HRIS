"""Allow-listed CareerHub application-source and UTM handling."""

from __future__ import annotations

import unicodedata

APPLICATION_SOURCES = (
    ("company_website", "Company website"),
    ("linkedin", "LinkedIn"),
    ("erasmusintern", "ErasmusIntern"),
    ("job_board", "Job board"),
    ("university_referral", "University referral"),
    ("referral", "Referral"),
)
APPLICATION_SOURCE_VALUES = {value for value, _label in APPLICATION_SOURCES}
DEFAULT_APPLICATION_SOURCE = "company_website"
PERMITTED_UTM_PARAMETERS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
)


def _safe_utm_value(value):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()[:100]
    if not value or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        return ""
    return value


def normalize_attribution(values, *, default_source=DEFAULT_APPLICATION_SOURCE):
    """Return only a known source and explicitly permitted, bounded UTM keys."""

    source = str(values.get("source", "")).strip().casefold()
    if source not in APPLICATION_SOURCE_VALUES:
        source = default_source
    utm_parameters = {
        key: normalized
        for key in PERMITTED_UTM_PARAMETERS
        if (normalized := _safe_utm_value(values.get(key)))
    }
    return {"source": source, "utm_parameters": utm_parameters}


def flatten_attribution(attribution):
    return {
        "source": attribution["source"],
        **attribution.get("utm_parameters", {}),
    }


def attribution_for_request(request):
    """Capture allow-listed attribution in the first-party candidate session."""

    keys = ("source", *PERMITTED_UTM_PARAMETERS)
    session = getattr(request, "session", None)
    if any(request.GET.get(key) for key in keys):
        attribution = normalize_attribution(request.GET)
        if session is not None:
            session["careerhub_application_attribution"] = attribution
        return attribution
    stored = session.get("careerhub_application_attribution", {}) if session else {}
    return normalize_attribution(
        {"source": stored.get("source"), **stored.get("utm_parameters", {})}
    )
