from django.urls import path

from tvg_careers.accounts import portal_views, views

urlpatterns = [
    path("sign-in/", views.candidate_login, name="candidate-login"),
    path("sign-out/", views.candidate_logout, name="candidate-logout"),
    path("create-account/", views.signup, name="candidate-signup"),
    path("verify/<str:token>/", views.verify_email, name="candidate-verify"),
    path("profile/", views.candidate_profile, name="candidate-profile"),
    path("dashboard/", portal_views.dashboard, name="candidate-dashboard"),
    path("documents/", portal_views.documents, name="candidate-documents"),
    path(
        "documents/<uuid:document_id>/download/",
        portal_views.document_download,
        name="candidate-document-download",
    ),
    path(
        "applications/start/<slug:venture_slug>/<slug:opening_slug>/",
        portal_views.application_create,
        name="candidate-application-create",
    ),
    path(
        "applications/<uuid:application_id>/withdraw/",
        portal_views.application_withdraw,
        name="candidate-application-withdraw",
    ),
    path(
        "applications/<uuid:application_id>/verification/"
        "<uuid:requirement_id>/upload/",
        portal_views.verification_upload,
        name="candidate-verification-upload",
    ),
    path(
        "applications/<uuid:application_id>/interviews/" "<uuid:invitation_id>/book/",
        portal_views.interview_book,
        name="candidate-interview-book",
    ),
    path(
        "applications/<uuid:application_id>/interviews/"
        "<uuid:invitation_id>/reschedule/",
        portal_views.interview_reschedule,
        name="candidate-interview-reschedule",
    ),
    path(
        "applications/<uuid:application_id>/arrangements/"
        "<uuid:case_id>/confirm-dates/",
        portal_views.arrangement_dates_confirm,
        name="candidate-arrangement-confirm-dates",
    ),
    path(
        "applications/<uuid:application_id>/arrangements/<uuid:case_id>/"
        "acceptance/<uuid:letter_id>/download/",
        portal_views.acceptance_download,
        name="candidate-acceptance-download",
    ),
    path(
        "applications/<uuid:application_id>/arrangements/<uuid:case_id>/"
        "acceptance/<uuid:letter_id>/receipt/",
        portal_views.acceptance_receipt,
        name="candidate-acceptance-receipt",
    ),
    path(
        "applications/<uuid:application_id>/erasmus/<uuid:case_id>/"
        "documents/<uuid:document_id>/upload/",
        portal_views.erasmus_document_upload,
        name="candidate-erasmus-document-upload",
    ),
    path(
        "applications/<uuid:application_id>/erasmus/<uuid:case_id>/"
        "documents/<uuid:document_id>/versions/<uuid:version_id>/download/",
        portal_views.erasmus_document_download,
        name="candidate-erasmus-document-download",
    ),
    path(
        "applications/<uuid:application_id>/erasmus/<uuid:case_id>/accommodation/",
        portal_views.accommodation_response,
        name="candidate-accommodation-response",
    ),
    path(
        "applications/<uuid:application_id>/documents/<slug:kind>/",
        portal_views.application_document_download,
        name="candidate-application-document",
    ),
    path(
        "password/reset/",
        views.CandidatePasswordResetView.as_view(),
        name="candidate-password-reset",
    ),
    path(
        "password/reset/sent/",
        views.CandidatePasswordResetDoneView.as_view(),
        name="candidate-password-reset-done",
    ),
    path(
        "password/reset/confirm/<uidb64>/<token>/",
        views.CandidatePasswordResetConfirmView.as_view(),
        name="candidate-password-reset-confirm",
    ),
    path(
        "password/reset/complete/",
        views.CandidatePasswordResetCompleteView.as_view(),
        name="candidate-password-reset-complete",
    ),
]
