"""Root URLconf for career.thevipgroup.eu; no Horilla staff routes are included."""

from django.urls import include, path

urlpatterns = [
    path(
        "",
        include(("tvg_careers.public_urls", "careerhub"), namespace="careerhub"),
    )
]
