"""Single-process notification dispatcher for internship reporting."""

from horilla.scheduling import register_job


def dispatch_reporting_notifications():
    from .services import dispatch_due_notifications

    dispatch_due_notifications()


register_job(
    dispatch_reporting_notifications,
    "interval",
    job_id="tvg_reporting_notifications",
    minutes=15,
)
