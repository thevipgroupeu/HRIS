from django.urls import path

from . import views

app_name = "completion"

urlpatterns = [
    path("", views.completion_home, name="home"),
    path("start/<uuid:onboarding_id>/", views.completion_start, name="start"),
    path("<uuid:public_id>/", views.completion_detail, name="detail"),
    path(
        "<uuid:public_id>/presentation/",
        views.presentation_schedule,
        name="presentation-schedule",
    ),
    path("<uuid:public_id>/reports/", views.report_link, name="report-link"),
    path("<uuid:public_id>/feedback/", views.final_feedback, name="feedback"),
    path("<uuid:public_id>/assets/return/", views.asset_return, name="asset-return"),
    path(
        "<uuid:public_id>/access/<uuid:task_id>/attempt/",
        views.access_attempt,
        name="access-attempt",
    ),
    path(
        "<uuid:public_id>/access/<uuid:task_id>/manual/",
        views.access_manual,
        name="access-manual",
    ),
    path(
        "<uuid:public_id>/access/<uuid:task_id>/exception/",
        views.access_exception,
        name="access-exception",
    ),
    path(
        "<uuid:public_id>/documents/prepare/",
        views.document_prepare,
        name="document-prepare",
    ),
    path(
        "<uuid:public_id>/documents/<uuid:document_id>/sign/",
        views.document_sign,
        name="document-sign",
    ),
    path(
        "<uuid:public_id>/documents/<uuid:document_id>/issue/",
        views.document_issue,
        name="document-issue",
    ),
    path(
        "<uuid:public_id>/documents/<uuid:document_id>/download/",
        views.document_download,
        name="document-download",
    ),
    path(
        "<uuid:public_id>/reference/request/",
        views.reference_request,
        name="reference-request",
    ),
    path(
        "<uuid:public_id>/reference/handle/",
        views.reference_handle,
        name="reference-handle",
    ),
    path(
        "<uuid:public_id>/requirements/<int:requirement_id>/exception/",
        views.requirement_exception,
        name="requirement-exception",
    ),
    path(
        "<uuid:public_id>/administrative-closure/",
        views.administrative_closure,
        name="administrative-closure",
    ),
    path("<uuid:public_id>/complete/", views.completion_finish, name="complete"),
]
