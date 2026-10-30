"""Single-process automation for internship completion and retention review."""

from horilla.scheduling import register_job


def run_completion_automation():
    from .services import (
        dispatch_due_notifications,
        mark_retention_reviews_due,
        materialize_upcoming_completions,
        reconcile_open_completions,
    )

    materialize_upcoming_completions()
    reconcile_open_completions()
    mark_retention_reviews_due()
    dispatch_due_notifications()


register_job(
    run_completion_automation,
    "interval",
    job_id="tvg_offboarding_completion_automation",
    minutes=15,
)
