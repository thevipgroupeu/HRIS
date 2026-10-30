from horilla.scheduling import register_job


def dispatch_automation_events():
    from .services import dispatch_due_events, materialize_existing_initial_events

    materialize_existing_initial_events()
    dispatch_due_events()


register_job(
    dispatch_automation_events,
    "interval",
    job_id="tvg_automation_outbox_dispatch",
    minutes=1,
)
