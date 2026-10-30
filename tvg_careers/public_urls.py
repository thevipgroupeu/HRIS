from django.apps import apps
from django.urls import include, path

from tvg_careers import public_views

app_name = "careerhub"

urlpatterns = [
    path("", public_views.landing, name="landing"),
    path("health/", public_views.health, name="health"),
    path("robots.txt", public_views.robots, name="robots"),
    path("sitemap.xml", public_views.sitemap, name="sitemap"),
    path("opportunities/", public_views.opportunities, name="opportunities"),
    path(
        "ventures/<slug:venture_slug>/",
        public_views.venture_opportunities,
        name="venture-opportunities",
    ),
    path(
        "opportunities/<slug:venture_slug>/<slug:opening_slug>/",
        public_views.opportunity_detail,
        name="opportunity-detail",
    ),
    path(
        "opportunities/<slug:venture_slug>/<slug:opening_slug>/apply/",
        public_views.application_start,
        name="application-start",
    ),
    path("internships/", public_views.internships, name="internships"),
    path("erasmus-plus/", public_views.erasmus, name="erasmus"),
    path("process/", public_views.process, name="process"),
    path("privacy/", public_views.privacy, name="privacy"),
]

if apps.is_installed("tvg_careers.accounts"):
    urlpatterns.insert(9, path("candidate/", include("tvg_careers.accounts.urls")))
else:
    # The private PeopleOps test/runtime does not install the public credential
    # store. Keep a no-input handoff only there so shared catalogue views can be
    # regression-tested without importing candidate account models into HR.
    urlpatterns.insert(
        9,
        path(
            "candidate/sign-in/",
            public_views.candidate_login_unavailable,
            name="candidate-login",
        ),
    )
