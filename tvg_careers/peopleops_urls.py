"""PeopleOps root URLconf with the narrow CareerHub integration endpoint."""

from django.http import HttpResponseNotFound
from django.urls import include, path

from horilla.urls import urlpatterns as horilla_urlpatterns


def legacy_candidate_tracking_disabled(request, *args, **kwargs):
    """Mask Horilla's email-plus-phone candidate authentication entry points."""

    return HttpResponseNotFound()


urlpatterns = [
    path(
        "peopleops/analytics/",
        include("tvg_analytics.urls"),
    ),
    path(
        "internal/automation/v1/",
        include("tvg_automation.urls"),
    ),
    path(
        "recruitment/candidate-login/",
        legacy_candidate_tracking_disabled,
        name="legacy-candidate-login-disabled",
    ),
    path(
        "recruitment/candidate-self-status-tracking/",
        legacy_candidate_tracking_disabled,
        name="legacy-candidate-tracking-disabled",
    ),
    path(
        "internal/careerhub/v1/",
        include("tvg_careers.internal_urls"),
    ),
    path(
        "recruitment/tvg-screening/",
        include("tvg_erasmus.urls"),
    ),
    path(
        "recruitment/tvg-interviews/",
        include("tvg_interviews.urls"),
    ),
    path(
        "onboarding/tvg-interns/",
        include("tvg_onboarding.urls"),
    ),
    *horilla_urlpatterns,
]
