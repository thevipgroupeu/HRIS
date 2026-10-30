from django.urls import path

from tvg_onboarding import views

app_name = "tvg_onboarding"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("start/<uuid:case_id>/", views.start, name="start"),
    path("<uuid:public_id>/", views.detail, name="detail"),
    path("<uuid:public_id>/approve/", views.approve, name="approve"),
    path(
        "<uuid:public_id>/provision-manual/",
        views.provision_manual,
        name="provision-manual",
    ),
    path("<uuid:public_id>/convert/", views.convert_employee, name="convert"),
    path("<uuid:public_id>/access/", views.grant_access, name="grant-access"),
    path("<uuid:public_id>/asset/", views.allocate, name="allocate"),
    path(
        "<uuid:public_id>/asset/<uuid:allocation_id>/prepare/",
        views.prepare_acknowledgment,
        name="prepare-acknowledgment",
    ),
    path(
        "<uuid:public_id>/asset/<uuid:allocation_id>/acknowledge/",
        views.acknowledge_asset,
        name="acknowledge-asset",
    ),
    path(
        "<uuid:public_id>/asset/<uuid:allocation_id>/download/",
        views.download_acknowledgment,
        name="download-acknowledgment",
    ),
    path(
        "<uuid:public_id>/task/<slug:code>/", views.complete_task, name="complete-task"
    ),
    path("<uuid:public_id>/finish/", views.finish, name="finish"),
]
