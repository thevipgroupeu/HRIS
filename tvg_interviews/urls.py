from django.urls import path

from tvg_interviews import views

app_name = "tvg_interviews"

urlpatterns = [
    path("", views.interview_dashboard, name="dashboard"),
    path(
        "applications/<uuid:application_id>/invite/",
        views.invite_candidate,
        name="invite",
    ),
    path(
        "bookings/<uuid:booking_id>/complete/",
        views.complete_booking,
        name="complete",
    ),
    path(
        "bookings/<uuid:booking_id>/feedback/",
        views.submit_feedback,
        name="feedback",
    ),
    path(
        "bookings/<uuid:booking_id>/decision/",
        views.submit_decision,
        name="decision",
    ),
    path(
        "bookings/<uuid:booking_id>/reschedule/",
        views.staff_reschedule,
        name="staff-reschedule",
    ),
    path(
        "bookings/<uuid:booking_id>/cancel/",
        views.cancel_booking,
        name="cancel",
    ),
]
