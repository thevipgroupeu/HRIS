from django.urls import path

from . import views

app_name = "peoplehub"

urlpatterns = [
    path("login/", views.PeopleHubLoginView.as_view(), name="login"),
    path("logout/", views.PeopleHubLogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("my-profile/", views.my_profile, name="my-profile"),
    path("directory/", views.directory, name="directory"),
    path("policies/<int:pk>/", views.policy_detail, name="policy-detail"),
    path("leave/request/", views.leave_create, name="leave-create"),
    path("requests/new/", views.request_create, name="request-create"),
    path("requests/queue/", views.request_queue, name="request-queue"),
    path("team/", views.team, name="team"),
    path("team/<int:pk>/", views.team_member, name="team-member"),
]
