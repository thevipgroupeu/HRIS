from django.urls import path

from tvg_careers import application_api, identity_api, internal_api
from tvg_erasmus import api as verification_api
from tvg_erasmus import arrangement_api, document_api
from tvg_interviews import api as interview_api

app_name = "tvg_careers_internal"

urlpatterns = [
    path("jobs/", internal_api.public_job_list, name="job-list"),
    path(
        "jobs/<slug:venture_slug>/<slug:opening_slug>/",
        internal_api.public_job_detail,
        name="job-detail",
    ),
    path(
        "identities/",
        identity_api.provision_person_identity,
        name="provision-person-identity",
    ),
    path(
        "applications/",
        application_api.submit_application,
        name="submit-application",
    ),
    path(
        "applications/status/",
        application_api.application_status,
        name="application-status",
    ),
    path(
        "applications/<uuid:application_id>/withdraw/",
        application_api.withdraw_application,
        name="withdraw-application",
    ),
    path(
        "applications/<uuid:application_id>/verification/"
        "<uuid:requirement_id>/evidence/",
        verification_api.upload_verification_evidence,
        name="verification-evidence-upload",
    ),
    path(
        "interviews/<uuid:invitation_id>/book/",
        interview_api.book_interview,
        name="interview-book",
    ),
    path(
        "interviews/<uuid:invitation_id>/reschedule/",
        interview_api.reschedule_interview,
        name="interview-reschedule",
    ),
    path(
        "arrangements/<uuid:case_id>/confirm-dates/",
        arrangement_api.confirm_dates,
        name="arrangement-confirm-dates",
    ),
    path(
        "arrangements/<uuid:case_id>/acceptance/<uuid:letter_id>/receipt/",
        arrangement_api.confirm_receipt,
        name="acceptance-confirm-receipt",
    ),
    path(
        "arrangements/<uuid:case_id>/acceptance/<uuid:letter_id>/download/",
        arrangement_api.download_acceptance,
        name="acceptance-download",
    ),
    path(
        "erasmus-cases/<uuid:case_id>/documents/<uuid:document_id>/upload/",
        document_api.upload_document,
        name="erasmus-document-upload",
    ),
    path(
        "erasmus-cases/<uuid:case_id>/documents/<uuid:document_id>/"
        "versions/<uuid:version_id>/download/",
        document_api.download_document,
        name="erasmus-document-download",
    ),
    path(
        "erasmus-cases/<uuid:case_id>/accommodation/",
        document_api.accommodation_response,
        name="erasmus-accommodation-response",
    ),
]
