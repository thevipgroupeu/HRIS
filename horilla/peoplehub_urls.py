from django.urls import include, path

from tvg_peoplehub.account_views import PeopleHubPasswordChangeView
from tvg_peoplehub.views import health, missing_file

urlpatterns = [
    path("health/", health, name="peoplehub-health"),
    path("_missing-file/", missing_file, name="404"),
    # This name/path is required by Horilla's first-login password gate.
    # It changes only the authenticated employee's password, never creates users.
    path(
        "change-password/",
        PeopleHubPasswordChangeView.as_view(),
        name="change-password",
    ),
    path("completion/", include("tvg_offboarding.urls")),
    path("reporting/", include("tvg_reporting.urls")),
    path("", include("tvg_peoplehub.urls")),
]

handler403 = "tvg_peoplehub.views.permission_denied"
handler404 = "tvg_peoplehub.views.page_not_found"
