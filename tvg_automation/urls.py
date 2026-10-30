from django.urls import path

from . import api

app_name = "tvg_automation"

urlpatterns = [
    path("executions/", api.execution_callback, name="execution-callback"),
]
