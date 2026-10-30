from __future__ import annotations

import json
from urllib.parse import urlencode, urljoin
from xml.sax.saxutils import escape

from django.apps import apps
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from tvg_careers.attribution import attribution_for_request, flatten_attribution
from tvg_careers.public_client import (
    CareerHubAPIError,
    CareerHubJobNotFound,
    get_public_job_client,
)
from tvg_careers.public_contract import PUBLIC_FILTER_KEYS

VENTURE_PRESENTATION = {
    "fluxops": {
        "name": "FluxOps",
        "eyebrow": "Industrial operations · AI · manufacturing software",
        "headline": "Build software for the factory floor.",
        "summary": (
            "Work on practical operations software, responsible industrial AI "
            "and the systems that make production work visible."
        ),
    },
    "findmystay": {
        "name": "FindMyStay",
        "eyebrow": "European accommodation marketplace",
        "headline": "Make an international move feel simpler.",
        "summary": (
            "Help build a trusted, multilingual marketplace for students and "
            "professionals arranging a home in another country."
        ),
    },
    "thevipgroup": {
        "name": "THEVIPGROUP",
        "eyebrow": "Company operations · people · technology",
        "headline": "Build the systems behind the group.",
        "summary": (
            "Improve the operational, people and technology foundations that "
            "support our products and international work."
        ),
    },
}


def _base_context(request, *, title, description, path=None):
    attribution_for_request(request)
    public_base = getattr(
        settings,
        "CAREERHUB_PUBLIC_BASE_URL",
        "https://career.thevipgroup.eu",
    ).rstrip("/")
    canonical_path = path or request.path
    return {
        "page_title": title,
        "meta_description": description,
        "canonical_url": urljoin(f"{public_base}/", canonical_path.lstrip("/")),
        "contact_email": getattr(
            settings,
            "CAREERHUB_CONTACT_EMAIL",
            "recruitment@thevipgroup.eu",
        ),
        "privacy_notice_version": getattr(
            settings,
            "CAREERHUB_PRIVACY_NOTICE_VERSION",
            "2026-10-25",
        ),
    }


def _filters_from_request(request):
    return {
        key: request.GET.get(key, "").strip()[:160]
        for key in PUBLIC_FILTER_KEYS
        if request.GET.get(key, "").strip()
    }


def _service_unavailable(request, context):
    context["service_unavailable"] = True
    return render(
        request,
        "careerhub/service_unavailable.html",
        context,
        status=503,
    )


@require_GET
def health(request):
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def landing(request):
    context = _base_context(
        request,
        title="Careers at THEVIPGROUP",
        description=(
            "Internships and technology opportunities across FluxOps, "
            "FindMyStay and THEVIPGROUP in Kraków and across Europe."
        ),
    )
    try:
        payload = get_public_job_client().list_jobs({"status": "open"})
    except CareerHubAPIError:
        return _service_unavailable(request, context)
    context["featured_jobs"] = payload["results"][:3]
    context["open_role_count"] = payload["count"]
    context["ventures"] = VENTURE_PRESENTATION
    return render(request, "careerhub/landing.html", context)


@require_GET
def opportunities(request):
    filters = _filters_from_request(request)
    context = _base_context(
        request,
        title="Opportunities",
        description=(
            "Search open internships and jobs across THEVIPGROUP, FluxOps "
            "and FindMyStay."
        ),
    )
    try:
        payload = get_public_job_client().list_jobs(filters)
    except CareerHubAPIError:
        return _service_unavailable(request, context)
    context.update(
        {
            "jobs": payload["results"],
            "job_count": payload["count"],
            "facets": payload["facets"],
            "active_filters": filters,
            "clear_filters_url": reverse("careerhub:opportunities"),
        }
    )
    return render(request, "careerhub/opportunities.html", context)


@require_GET
def venture_opportunities(request, venture_slug):
    presentation = VENTURE_PRESENTATION.get(venture_slug)
    if not presentation:
        raise Http404
    filters = _filters_from_request(request)
    filters["venture"] = venture_slug
    context = _base_context(
        request,
        title=f"{presentation['name']} opportunities",
        description=presentation["summary"],
    )
    try:
        payload = get_public_job_client().list_jobs(filters)
    except CareerHubAPIError:
        return _service_unavailable(request, context)
    context.update(
        {
            "jobs": payload["results"],
            "job_count": payload["count"],
            "facets": payload["facets"],
            "active_filters": filters,
            "venture_page": presentation,
            "venture_slug": venture_slug,
            "clear_filters_url": reverse(
                "careerhub:venture-opportunities",
                kwargs={"venture_slug": venture_slug},
            ),
        }
    )
    return render(request, "careerhub/opportunities.html", context)


def _job_or_404(venture_slug, opening_slug):
    try:
        return get_public_job_client().get_job(venture_slug, opening_slug)
    except CareerHubJobNotFound as exc:
        raise Http404 from exc


def _safe_json(value):
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


@require_GET
def opportunity_detail(request, venture_slug, opening_slug):
    try:
        job = _job_or_404(venture_slug, opening_slug)
    except CareerHubAPIError:
        context = _base_context(
            request,
            title="Opportunity temporarily unavailable",
            description="The opportunity service is temporarily unavailable.",
        )
        return _service_unavailable(request, context)

    context = _base_context(
        request,
        title=f"{job['title']} · {job['venture']['name']}",
        description=job["role_description"][:155],
        path=job["url_path"],
    )
    schema = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job["title"],
        "description": job["role_description"],
        "datePosted": job["application_opening_date"],
        "validThrough": job["application_closing_date"],
        "employmentType": (
            "INTERN" if job["category"] == "internship" else "FULL_TIME"
        ),
        "hiringOrganization": {
            "@type": "Organization",
            "name": job["legal_employer"],
            "sameAs": "https://thevipgroup.eu/",
        },
        "jobLocationType": ("TELECOMMUTE" if job["work_mode"] == "remote" else None),
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": job["location"],
                "addressCountry": "PL",
            },
        },
        "identifier": {
            "@type": "PropertyValue",
            "name": job["legal_employer"],
            "value": job["public_id"],
        },
    }
    context.update(
        {
            "job": job,
            "job_schema_json": _safe_json(
                {key: value for key, value in schema.items() if value is not None}
            ),
            "application_closed_notice": request.GET.get("application") == "closed",
        }
    )
    return render(request, "careerhub/opportunity_detail.html", context)


@require_GET
def application_start(request, venture_slug, opening_slug):
    try:
        job = _job_or_404(venture_slug, opening_slug)
    except CareerHubAPIError:
        raise Http404
    detail_url = reverse(
        "careerhub:opportunity-detail",
        kwargs={"venture_slug": venture_slug, "opening_slug": opening_slug},
    )
    if not job["accepting_applications"]:
        return redirect(f"{detail_url}?application=closed")
    if apps.is_installed("tvg_careers.accounts"):
        application_url = reverse(
            "careerhub:candidate-application-create",
            kwargs={"venture_slug": venture_slug, "opening_slug": opening_slug},
        )
        attribution_query = urlencode(
            flatten_attribution(attribution_for_request(request))
        )
        application_url = f"{application_url}?{attribution_query}"
    else:
        application_url = detail_url
    request_user = getattr(request, "user", None)
    if (
        request_user
        and request_user.is_authenticated
        and apps.is_installed("tvg_careers.accounts")
    ):
        return redirect(application_url)
    query = urlencode({"job": job["public_id"], "next": application_url})
    return redirect(f"{reverse('careerhub:candidate-login')}?{query}")


@require_GET
def candidate_login_unavailable(request):
    """Non-credential fallback used only when the public account app is absent."""

    context = _base_context(
        request,
        title="Candidate portal",
        description="Secure candidate sign-in is available on CareerHub.",
    )
    context["robots"] = "noindex, nofollow"
    response = render(request, "careerhub/candidate_login_unavailable.html", context)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def internships(request):
    context = _base_context(
        request,
        title="Internships at THEVIPGROUP",
        description=(
            "Practical, supervised internships across technology, product, "
            "operations and people systems."
        ),
    )
    return render(request, "careerhub/internships.html", context)


@require_GET
def erasmus(request):
    context = _base_context(
        request,
        title="Erasmus+ internships",
        description=(
            "Information for students considering an Erasmus+ traineeship "
            "with THEVIPGROUP in Kraków."
        ),
    )
    return render(request, "careerhub/erasmus.html", context)


@require_GET
def process(request):
    context = _base_context(
        request,
        title="Recruitment process and FAQs",
        description=(
            "What to expect when applying for an internship or job with " "THEVIPGROUP."
        ),
    )
    return render(request, "careerhub/process.html", context)


@require_GET
def privacy(request):
    context = _base_context(
        request,
        title="Privacy and contact",
        description=(
            "CareerHub privacy information and recruitment contact details "
            "for THEVIPGROUP Sp. z o.o."
        ),
    )
    return render(request, "careerhub/privacy.html", context)


@require_GET
def robots(request):
    sitemap_url = urljoin(
        f"{settings.CAREERHUB_PUBLIC_BASE_URL.rstrip('/')}/",
        "sitemap.xml",
    )
    return HttpResponse(
        f"User-agent: *\nAllow: /\nDisallow: /candidate/\nSitemap: {sitemap_url}\n",
        content_type="text/plain",
    )


@require_GET
def sitemap(request):
    public_base = settings.CAREERHUB_PUBLIC_BASE_URL.rstrip("/")
    try:
        payload = get_public_job_client().list_jobs()
    except CareerHubAPIError:
        return HttpResponse(status=503)

    paths = [
        reverse("careerhub:landing"),
        reverse("careerhub:opportunities"),
        reverse("careerhub:internships"),
        reverse("careerhub:erasmus"),
        reverse("careerhub:process"),
        reverse("careerhub:privacy"),
        *[
            reverse(
                "careerhub:venture-opportunities",
                kwargs={"venture_slug": venture_slug},
            )
            for venture_slug in VENTURE_PRESENTATION
        ],
        *[job["url_path"] for job in payload["results"]],
    ]
    urls = "".join(
        f"<url><loc>{escape(urljoin(f'{public_base}/', path.lstrip('/')))}</loc></url>"
        for path in paths
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")
