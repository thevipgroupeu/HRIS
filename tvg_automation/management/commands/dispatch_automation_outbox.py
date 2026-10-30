from django.core.management.base import BaseCommand

from tvg_automation.services import (
    dispatch_due_events,
    materialize_existing_initial_events,
)


class Command(BaseCommand):
    help = (
        "Dispatch due signed lifecycle events to the configured Activepieces webhook."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        materialize_existing_initial_events()
        count = dispatch_due_events(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Delivered {count} lifecycle event(s)."))
