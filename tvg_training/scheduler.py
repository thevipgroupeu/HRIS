from horilla.scheduling import register_job


def queue_training_overdue_notifications():
    from .services import queue_overdue_training_notifications

    queue_overdue_training_notifications()


register_job(
    queue_training_overdue_notifications,
    "interval",
    job_id="tvg_training_overdue_notifications",
    hours=6,
)
