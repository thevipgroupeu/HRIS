"""Read-only, field-allow-listed projection for the CareerHub integration API."""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from tvg_careers.models import JobOpening
from tvg_careers.public_contract import PUBLIC_FILTER_KEYS
from tvg_careers.questionnaires import (
    QuestionnaireConfigurationError,
    current_questionnaire_version,
    public_questionnaire,
    questionnaire_version_payload,
)


def _legal_employer_id():
    value = getattr(settings, "TVG_LEGAL_EMPLOYER_ID", None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def published_openings():
    """Return only explicitly published rows for the configured legal employer."""

    queryset = JobOpening.all_objects.none()
    employer_id = _legal_employer_id()
    if not employer_id:
        return queryset

    return (
        JobOpening.all_objects.filter(
            is_active=True,
            venture__is_active=True,
            venture__legal_employer_id=employer_id,
            venture__legal_employer__is_active=True,
            recruitment__is_active=True,
            recruitment__is_published=True,
            recruitment__closed=False,
        )
        .select_related(
            "venture",
            "venture__legal_employer",
            "recruitment",
            "recruitment__job_position_id",
            "recruitment__job_position_id__department_id",
        )
        .prefetch_related("preferred_skills", "recruitment__skills")
    )


def normalize_filters(raw_filters):
    normalized = {}
    for key in PUBLIC_FILTER_KEYS:
        value = raw_filters.get(key, "")
        value = str(value).strip()[:160]
        if value:
            normalized[key] = value
    return normalized


def filter_openings(queryset, filters, *, on_date=None):
    filters = normalize_filters(filters)
    on_date = on_date or timezone.localdate()

    if venture := filters.get("venture"):
        queryset = queryset.filter(venture__slug=venture)
    if department := filters.get("department"):
        queryset = queryset.filter(
            recruitment__job_position_id__department_id__department__iexact=department
        )
    if category := filters.get("category"):
        queryset = queryset.filter(opportunity_type=category)
    if location := filters.get("location"):
        queryset = queryset.filter(work_location__icontains=location)
    if work_mode := filters.get("work_mode"):
        queryset = queryset.filter(work_mode=work_mode)
    if erasmus := filters.get("erasmus"):
        if erasmus.lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(erasmus_plus_applicable=True)
        elif erasmus.lower() in {"0", "false", "no"}:
            queryset = queryset.filter(erasmus_plus_applicable=False)
    if keyword := filters.get("q"):
        queryset = queryset.filter(
            Q(recruitment__title__icontains=keyword)
            | Q(recruitment__description__icontains=keyword)
            | Q(responsibilities__icontains=keyword)
            | Q(learning_outcomes__icontains=keyword)
            | Q(work_location__icontains=keyword)
            | Q(venture__name__icontains=keyword)
            | Q(recruitment__skills__title__icontains=keyword)
            | Q(preferred_skills__title__icontains=keyword)
        )

    status = filters.get("status")
    if status == "open":
        queryset = queryset.filter(recruitment__start_date__lte=on_date).filter(
            Q(recruitment__end_date__isnull=True)
            | Q(recruitment__end_date__gte=on_date)
        )
    elif status == "upcoming":
        queryset = queryset.filter(recruitment__start_date__gt=on_date)
    elif status == "closed":
        queryset = queryset.filter(recruitment__end_date__lt=on_date)

    return queryset.distinct()


def application_status(opening, *, on_date=None):
    on_date = on_date or timezone.localdate()
    if opening.application_opening_date > on_date:
        return "upcoming"
    closing_date = opening.application_closing_date
    if closing_date and closing_date < on_date:
        return "closed"
    return "open"


def serialize_opening(opening, *, on_date=None):
    """Serialize only fields approved for anonymous CareerHub visitors."""

    on_date = on_date or timezone.localdate()
    status = application_status(opening, on_date=on_date)
    position = opening.job_position
    department = position.department_id if position else None
    questionnaire_supported = True
    try:
        version = current_questionnaire_version(opening.recruitment)
    except QuestionnaireConfigurationError:
        version = None
        questionnaire_supported = False
    if version is None:
        questionnaire_supported = False
    questionnaire = public_questionnaire(version) if version else []
    accepting_applications = (
        opening.is_publicly_available(on_date) and questionnaire_supported
    )
    return {
        "public_id": str(opening.public_id),
        "slug": opening.public_slug,
        "title": opening.title,
        "role_description": opening.role_description,
        "responsibilities": opening.responsibilities,
        "learning_outcomes": opening.learning_outcomes,
        "venture": {
            "name": opening.venture.name,
            "slug": opening.venture.slug,
            "summary": opening.venture.summary,
        },
        "legal_employer": opening.venture.legal_employer.company,
        "department": department.department if department else "",
        "job_position": position.job_position if position else "",
        "category": opening.opportunity_type,
        "category_label": opening.get_opportunity_type_display(),
        "required_skills": sorted(
            skill.title for skill in opening.recruitment.skills.all()
        ),
        "preferred_skills": sorted(
            skill.title for skill in opening.preferred_skills.all()
        ),
        "location": opening.work_location,
        "work_mode": opening.work_mode,
        "work_mode_label": opening.get_work_mode_display(),
        "erasmus_plus": opening.erasmus_plus_applicable,
        "expected_start_date_from": opening.expected_start_date_from.isoformat(),
        "expected_start_date_to": (
            opening.expected_start_date_to.isoformat()
            if opening.expected_start_date_to
            else None
        ),
        "minimum_duration_weeks": opening.minimum_duration_weeks,
        "preferred_duration_weeks": opening.preferred_duration_weeks,
        "number_of_positions": opening.number_of_positions,
        "application_opening_date": opening.application_opening_date.isoformat(),
        "application_closing_date": (
            opening.application_closing_date.isoformat()
            if opening.application_closing_date
            else None
        ),
        "application_status": status,
        "accepting_applications": accepting_applications,
        "questionnaire_supported": questionnaire_supported,
        "questionnaire_version": (
            questionnaire_version_payload(version) if version else None
        ),
        "questionnaire": questionnaire,
        "url_path": (f"/opportunities/{opening.venture.slug}/{opening.public_slug}/"),
    }


def list_public_openings(raw_filters, *, on_date=None):
    on_date = on_date or timezone.localdate()
    filters = normalize_filters(raw_filters)
    base_queryset = published_openings()
    queryset = filter_openings(base_queryset, filters, on_date=on_date)
    results = [
        serialize_opening(opening, on_date=on_date) for opening in queryset[:100]
    ]

    facet_rows = list(
        base_queryset.values(
            "venture__slug",
            "venture__name",
            "recruitment__job_position_id__department_id__department",
            "opportunity_type",
            "work_location",
            "work_mode",
        ).distinct()
    )
    facets = {
        "ventures": sorted(
            {(row["venture__slug"], row["venture__name"]) for row in facet_rows},
            key=lambda item: item[1].casefold(),
        ),
        "departments": sorted(
            {
                row["recruitment__job_position_id__department_id__department"]
                for row in facet_rows
                if row["recruitment__job_position_id__department_id__department"]
            },
            key=str.casefold,
        ),
        "categories": list(JobOpening.OpportunityType.choices),
        "locations": sorted(
            {row["work_location"] for row in facet_rows if row["work_location"]},
            key=str.casefold,
        ),
        "work_modes": list(JobOpening.WorkMode.choices),
        "statuses": [
            ("open", "Open now"),
            ("upcoming", "Opening soon"),
            ("closed", "Applications closed"),
        ],
    }
    return {
        "results": results,
        "count": len(results),
        "filters": filters,
        "facets": facets,
    }


def get_public_opening(*, venture_slug, opening_slug, on_date=None):
    opening = published_openings().get(
        venture__slug=venture_slug,
        public_slug=opening_slug,
    )
    return serialize_opening(opening, on_date=on_date)
