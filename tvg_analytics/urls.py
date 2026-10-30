from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.executive_dashboard, name="executive"),
    path("recruitment/", views.recruitment_dashboard, name="recruitment"),
    path("snapshots/create/", views.snapshot_create, name="snapshot-create"),
    path(
        "snapshots/<uuid:public_id>/",
        views.snapshot_detail,
        name="snapshot-detail",
    ),
    path(
        "snapshots/<uuid:public_id>/export/",
        views.snapshot_export,
        name="snapshot-export",
    ),
]
