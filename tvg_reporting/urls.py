from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.reporting_home, name="home"),
    path("weekly/new/", views.weekly_edit, name="weekly-new"),
    path("weekly/<uuid:public_id>/", views.weekly_detail, name="weekly-detail"),
    path("weekly/<uuid:public_id>/edit/", views.weekly_edit, name="weekly-edit"),
    path("weekly/<uuid:public_id>/submit/", views.weekly_submit, name="weekly-submit"),
    path("weekly/<uuid:public_id>/review/", views.weekly_review, name="weekly-review"),
    path("presentations/new/", views.presentation_edit, name="presentation-new"),
    path(
        "presentations/<uuid:public_id>/",
        views.presentation_detail,
        name="presentation-detail",
    ),
    path(
        "presentations/<uuid:public_id>/edit/",
        views.presentation_edit,
        name="presentation-edit",
    ),
    path(
        "presentations/<uuid:public_id>/submit/",
        views.presentation_submit,
        name="presentation-submit",
    ),
    path(
        "presentations/<uuid:public_id>/review/",
        views.presentation_review,
        name="presentation-review",
    ),
    path(
        "presentations/versions/<int:version_id>/download/",
        views.presentation_download,
        name="presentation-download",
    ),
    path("reviews/", views.review_queue, name="review-queue"),
    path("management/", views.management, name="management"),
]
